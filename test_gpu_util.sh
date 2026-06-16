#!/usr/bin/env bash
# Empirically test recipes through the Hub daemon (same flow as the UI Launch
# button): launch -> wait for daemon `ready` -> read vLLM's own KV-cache /
# max-concurrency log line (the proof full context fits) -> quick generation
# probe -> stop. No raw `docker compose up`, no in-container scripts.
set -u
# Hard single-instance lock: refuse to start if another sweep already holds it.
# (Two concurrent sweeps fight over port 9001 and rm -f each other's containers.)
exec 9>/tmp/gpu_util_sweep.lock
if ! flock -n 9; then
  echo "ABORT: another gpu_util sweep already holds /tmp/gpu_util_sweep.lock" >&2
  exit 3
fi
API=http://127.0.0.1:9000
OUT=/tmp/gpu_util_test_results.md
TIMEOUT=${TIMEOUT:-600}   # seconds to wait for ready per model

jget(){ curl -s -m 8 "$API/api/recipes/$1"; }
field(){ python3 -c "import sys,json;print(json.load(sys.stdin).get('$1',''))"; }

mem_avail_gib(){ free -g | awk '/^Mem:/{print $7}'; }

stop_slug(){
  local cont="spark-ai-hub-$1"
  curl -s -m 30 -X POST "$API/api/recipes/$1/stop" >/dev/null 2>&1
  # belt-and-suspenders: kill any lingering / crash-looping container directly
  docker rm -f "$cont" >/dev/null 2>&1
  # wait for unified memory to actually free before the next heavy launch
  for _ in $(seq 1 45); do
    docker ps -a --format '{{.Names}}' | grep -q "^$cont$" && { sleep 2; continue; }
    [ "$(mem_avail_gib)" -ge 85 ] && break
    sleep 2
  done
}

test_slug(){
  local slug="$1"
  local cont="spark-ai-hub-$slug"
  echo "### $slug" | tee -a "$OUT"
  # ensure clean slate: stop everything heavy on the shared GPU
  stop_slug "$slug"
  local f="registry/recipes/$slug/docker-compose.yml"
  local gutil mlen
  gutil=$(python3 -c "import re,sys;t=open('$f').read();m=re.search(r'gpu-memory-utilization\s*\n(?:\s*#.*\n)*\s*-\s*\"?([\d.]+)',t);print(m.group(1) if m else '?')" 2>/dev/null)
  mlen=$(python3 -c "import re,sys;t=open('$f').read();m=re.search(r'(?:max-model-len|max-seq-len)\s*\n(?:\s*#.*\n)*\s*-\s*\"?([\d]+)',t);print(m.group(1) if m else '?')" 2>/dev/null)
  curl -s -m 60 -X POST "$API/api/recipes/$slug/launch" >/dev/null 2>&1
  local ready="" t0=$SECONDS
  while [ $((SECONDS-t0)) -lt "$TIMEOUT" ]; do
    local j; j=$(jget "$slug")
    [ "$(echo "$j" | field ready)" = "True" ] && { ready=yes; break; }
    # crash-loop detection: container restarting or exited => fail fast (don't
    # let a crash-looping container sit there eating memory for the full timeout)
    local cstate; cstate=$(docker inspect -f '{{.State.Status}} {{.RestartCount}}' "$cont" 2>/dev/null)
    case "$cstate" in
      restarting*) ready=crashed; break;;
      exited*) ready=crashed; break;;
    esac
    if [ "$(echo "$j" | field running)" != "True" ] && [ "$(echo "$j" | field starting)" != "True" ] && [ $((SECONDS-t0)) -gt 25 ]; then
      if ! docker ps --format '{{.Names}}' | grep -q "^$cont$"; then ready=crashed; break; fi
    fi
    sleep 4
  done
  local kvmem kvtok conc err
  kvmem=$(docker logs "$cont" 2>&1 | grep -oiE 'Available KV cache memory: [0-9.]+ GiB' | tail -1)
  kvtok=$(docker logs "$cont" 2>&1 | grep -oiE 'GPU KV cache size: [0-9,]+ tokens' | tail -1)
  conc=$(docker logs "$cont" 2>&1 | grep -oiE 'Maximum concurrency for [0-9,]+ tokens per request: [0-9.]+x' | tail -1)
  err=$(docker logs "$cont" 2>&1 | grep -iE 'ValueError|out of memory|CUDA error|larger than the maximum number|No available memory for the cache|insufficient' | tail -1)
  if [ "$ready" = yes ]; then
    # quick generation probe through the served OpenAI API (port 9001 heavy / else 8000)
    local gen
    gen=$(curl -s -m 60 http://127.0.0.1:9001/v1/completions -H 'Content-Type: application/json' \
      -d '{"model":"","prompt":"Reply with the single word OK.","max_tokens":8,"temperature":0}' 2>/dev/null | head -c 300)
    printf -- '- gpu_util=%s max_len=%s -> **READY**\n  - %s\n  - %s\n  - %s\n  - gen: `%s`\n\n' \
      "$gutil" "$mlen" "${kvmem:-n/a}" "${kvtok:-n/a}" "${conc:-n/a}" "${gen:0:160}" | tee -a "$OUT"
  else
    # save the FULL container log so a real cause can be diagnosed after stop
    docker logs "$cont" > "/tmp/fail-$slug.log" 2>&1 || true
    printf -- '- gpu_util=%s max_len=%s -> **FAIL (%s)**\n  - %s\n  - err: `%s`\n  - full log: /tmp/fail-%s.log\n\n' \
      "$gutil" "$mlen" "${ready:-timeout}" "${conc:-no-concurrency-line}" "${err:-see logs}" "$slug" | tee -a "$OUT"
  fi
  stop_slug "$slug"
}

for s in "$@"; do test_slug "$s"; done
echo "done" | tee -a "$OUT"
