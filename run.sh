#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[spark-ai-hub] Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[spark-ai-hub] Installing Python dependencies..."
pip install -q -r requirements.txt

# The daemon binds 9010; the Caddy container it starts serves the Hub, its API
# and every app on 9000. That single port is the whole public surface -- one
# Cloudflare Tunnel origin, no per-app ports. 9010 stays reachable directly as
# the recovery door for when the proxy itself is the thing that is broken.
echo "[spark-ai-hub] Starting Spark AI Hub — UI on :9000, daemon on :9010..."
exec uvicorn daemon.main:app --host 0.0.0.0 --port 9010
