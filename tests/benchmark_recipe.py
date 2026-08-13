#!/usr/bin/env python3
"""Benchmark any Hub LLM recipe through the interface, exactly the way a user would.

Same methodology as benchmark_via_interface.py (3 prompts, 512 max_tokens,
temperature 0, warmup discarded) but generic over the recipe slug: launch via
the daemon API, poll the daemon for readiness, hit the OpenAI endpoint the Hub
exposes, then stop via the daemon API.

    python3 tests/benchmark_recipe.py vllm-ling3-tiny-bf16 [vllm-ling3-tiny-int4 ...]
"""

import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HUB_API = "http://127.0.0.1:9000"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "registry" / "recipes"

BENCHMARK_PROMPTS = [
    ("code", "Write a quicksort implementation in Python with comments explaining each step."),
    ("explainer", "Explain the differences between TCP and UDP, and when each is preferred."),
    ("prose", "Plan a 3-day cultural travel itinerary for Kyoto, Japan in autumn."),
]


def recipe_field(slug: str, field: str) -> str | None:
    content = (RECIPES_DIR / slug / "recipe.yaml").read_text()
    m = re.search(rf'^\s*{field}:\s*"?([^"\n]+)"?\s*$', content, re.MULTILINE)
    return m.group(1).strip() if m else None


def api(path: str, method: str = "GET", timeout: int = 30):
    req = urllib.request.Request(
        f"{HUB_API}{path}",
        data=b"{}" if method == "POST" else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def wait_ready(slug: str, timeout: int = 900) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            if api(f"/api/recipes/{slug}").get("ready"):
                print(f"  ready in {int(time.time() - start)}s")
                return True
        except Exception:
            pass
        time.sleep(3)
    print(f"  TIMEOUT after {timeout}s")
    return False


def chat(port: str, model: str, prompt: str, max_tokens: int, timeout: int = 600):
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
    }).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"},
    )
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode())
    return data, time.time() - start


def benchmark(slug: str) -> float | None:
    model = recipe_field(slug, "model_id")
    port = recipe_field(slug, "port") or "9001"
    print(f"\n=== {slug}  (model={model}, port={port})")

    print("  launching via Hub API...")
    api(f"/api/recipes/{slug}/launch", "POST")
    if not wait_ready(slug):
        return None

    try:
        print("  warmup...", end="", flush=True)
        chat(port, model, "hi", 32, timeout=180)
        print(" done")

        total_tokens = total_time = 0.0
        for label, prompt in BENCHMARK_PROMPTS:
            data, elapsed = chat(port, model, prompt, 512)
            tokens = data["usage"]["completion_tokens"]
            total_tokens += tokens
            total_time += elapsed
            print(f"    {label:<10} {tokens} tok / {elapsed:.2f}s = {tokens / elapsed:.2f} tok/s")

        avg = total_tokens / total_time if total_time else 0.0
        print(f"  AVERAGE: {avg:.2f} tok/s ({int(total_tokens)} tok / {total_time:.2f}s)")
        return avg
    except Exception as e:
        print(f"  benchmark failed: {e}")
        return None
    finally:
        print("  stopping via Hub API...")
        try:
            api(f"/api/recipes/{slug}/stop", "POST")
        except Exception as e:
            print(f"  stop failed: {e}")


if __name__ == "__main__":
    results = {slug: benchmark(slug) for slug in sys.argv[1:]}
    print("\n=== summary")
    for slug, avg in results.items():
        print(f"  {slug:<32} {'FAILED' if avg is None else f'{avg:.1f} tok/s'}")
