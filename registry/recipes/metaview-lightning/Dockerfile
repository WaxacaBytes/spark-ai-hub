# MetaView Lightning — native aarch64 / CUDA 13 build for the DGX Spark (GB10).
# Same proven base + torch cu130 stack as qwen-image-perspective, but self
# contained (no shared base image) so a Hub uninstall frees every byte.
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1
ENV PIP_PREFER_BINARY=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    git curl \
    libglib2.0-0 libgl1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:${PATH}"

# 1) Torch/vision/audio from the CUDA 13.0 wheel index (proven on GB10).
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir torch torchvision torchaudio \
      --index-url https://download.pytorch.org/whl/cu130

# 2) The rest of the runtime deps (no torch* here — see requirements.txt).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 3) App code: the ZeroGPU `spaces` shim first (import order matters — app.py
#    imports it before torch), then the vendored pipeline + the app itself.
COPY spaces /app/spaces
COPY diffsynth /app/diffsynth
COPY depth_anything_3 /app/depth_anything_3
COPY src /app/src
COPY examples /app/examples
# Vendored Three.js r128 for the offline 3D camera widget (inlined by app.py).
COPY three.min.js /app/three.min.js
COPY app.py /app/app.py

RUN mkdir -p /data/output /data-nvme/zerogpu_tensors

EXPOSE 7860

CMD ["python", "/app/app.py"]
