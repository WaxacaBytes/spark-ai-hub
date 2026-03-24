#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[sparkdeck] Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "[sparkdeck] Installing Python dependencies..."
pip install -q -r requirements.txt

echo "[sparkdeck] Starting SparkDeck on port 9000..."
exec uvicorn daemon.main:app --host 0.0.0.0 --port 9000
