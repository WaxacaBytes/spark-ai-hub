#!/usr/bin/env bash
set -e

REPO="https://github.com/WaxacaBytes/sparkdeck.git"
INSTALL_DIR="$HOME/sparkdeck"
PORT=9000

echo "  SparkDeck Installer"
echo "  ==================="
echo ""

# Check dependencies
for cmd in git python3 docker; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd is required but not installed." >&2
        exit 1
    fi
done

# Clone or update
if [ -d "$INSTALL_DIR" ]; then
    echo "[sparkdeck] Updating existing installation..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "[sparkdeck] Cloning SparkDeck..."
    git clone "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# Python venv + deps
if [ ! -d ".venv" ]; then
    echo "[sparkdeck] Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "[sparkdeck] Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "[sparkdeck] Installation complete!"
echo "[sparkdeck] Starting SparkDeck on port $PORT..."
echo "[sparkdeck] Open http://localhost:$PORT in your browser"
echo ""

exec uvicorn daemon.main:app --host 0.0.0.0 --port "$PORT"
