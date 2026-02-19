#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[sparkforge] Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[sparkforge] Installing Python dependencies..."
pip install -q -r requirements.txt

echo "[sparkforge] Starting SparkForge on port 9000..."
exec uvicorn daemon.main:app --host 0.0.0.0 --port 9000
