#!/bin/bash
set -e

CHECKPOINT_DIR="/app/ComfyUI/models/checkpoints"
SD15_FILE="$CHECKPOINT_DIR/v1-5-pruned-emaonly-fp16.safetensors"

# Download default checkpoint if no models exist
if [ -z "$(ls -A $CHECKPOINT_DIR 2>/dev/null)" ]; then
    echo "[comfyui-spark] No checkpoints found. Downloading SD 1.5 (~2GB)..."
    wget -q --show-progress -O "$SD15_FILE" \
        "https://huggingface.co/Comfy-Org/stable-diffusion-v1-5-archive/resolve/main/v1-5-pruned-emaonly-fp16.safetensors" \
        || echo "[comfyui-spark] Warning: checkpoint download failed, continuing without it"
    echo "[comfyui-spark] Checkpoint download complete."
else
    echo "[comfyui-spark] Checkpoints directory not empty, skipping download."
fi

echo "[comfyui-spark] Starting ComfyUI..."
exec python3 main.py --listen 0.0.0.0 "$@"
