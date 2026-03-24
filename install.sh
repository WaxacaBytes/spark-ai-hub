#!/usr/bin/env bash
set -e

REPO="https://github.com/WaxacaBytes/sparkdeck.git"
INSTALL_DIR="$HOME/sparkdeck"
PORT=9000

echo ""
echo "  SparkDeck Installer"
echo "  ==================="
echo ""

# ---------- helpers ----------

need_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

apt_updated=false
ensure_apt_updated() {
    if [ "$apt_updated" = false ]; then
        echo "[sparkdeck] Updating package lists..."
        need_sudo apt-get update -qq
        apt_updated=true
    fi
}

# ---------- git ----------

if ! command -v git &>/dev/null; then
    echo "[sparkdeck] Installing git..."
    ensure_apt_updated
    need_sudo apt-get install -y -qq git
fi

# ---------- python3 + venv ----------

if ! command -v python3 &>/dev/null; then
    echo "[sparkdeck] Installing python3..."
    ensure_apt_updated
    need_sudo apt-get install -y -qq python3 python3-venv python3-pip
elif ! python3 -m venv --help &>/dev/null 2>&1; then
    echo "[sparkdeck] Installing python3-venv..."
    ensure_apt_updated
    need_sudo apt-get install -y -qq python3-venv
fi

# ---------- docker ----------

if ! command -v docker &>/dev/null; then
    echo "[sparkdeck] Installing Docker Engine..."
    ensure_apt_updated
    need_sudo apt-get install -y -qq ca-certificates curl

    # Add Docker official GPG key and repo
    need_sudo install -m 0755 -d /etc/apt/keyrings
    need_sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    need_sudo chmod a+r /etc/apt/keyrings/docker.asc

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
        need_sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    need_sudo apt-get update -qq
    need_sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

    # Let current user run docker without sudo
    need_sudo usermod -aG docker "$USER"
    echo "[sparkdeck] Added $USER to docker group (takes effect on next login or after 'newgrp docker')"
fi

# ---------- nvidia-container-toolkit ----------

if ! dpkg -s nvidia-container-toolkit &>/dev/null 2>&1; then
    echo "[sparkdeck] Installing NVIDIA Container Toolkit..."
    if [ ! -f /etc/apt/sources.list.d/nvidia-container-toolkit.list ]; then
        curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
            need_sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
        curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
            sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' | \
            need_sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
        need_sudo apt-get update -qq
    fi
    need_sudo apt-get install -y -qq nvidia-container-toolkit
    need_sudo nvidia-ctk runtime configure --runtime=docker
    need_sudo systemctl restart docker
fi

# ---------- clone or update ----------

if [ -d "$INSTALL_DIR" ]; then
    echo "[sparkdeck] Updating existing installation..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "[sparkdeck] Cloning SparkDeck..."
    git clone "$REPO" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

# ---------- python venv + deps ----------

if [ ! -d ".venv" ]; then
    echo "[sparkdeck] Creating virtual environment..."
    python3 -m venv .venv
fi
source .venv/bin/activate
echo "[sparkdeck] Installing Python dependencies..."
pip install -q -r requirements.txt

# ---------- launch ----------

echo ""
echo "[sparkdeck] Installation complete!"
echo "[sparkdeck] Starting SparkDeck on port $PORT..."
echo "[sparkdeck] Open http://localhost:$PORT in your browser"
echo ""

exec uvicorn daemon.main:app --host 0.0.0.0 --port "$PORT"
