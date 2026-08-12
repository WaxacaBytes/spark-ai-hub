# --- weights stage ---------------------------------------------------
# Model weights are part of the app artifact: baked into the final image
# below, so `docker rmi` reclaims every byte this recipe introduced.
FROM python:3.13-slim AS weights
ENV HF_HUB_DISABLE_XET=1 HF_HOME=/weights
RUN pip install --no-cache-dir huggingface_hub
RUN --mount=type=secret,id=hf_token \
    HF_TOKEN="$(cat /run/secrets/hf_token 2>/dev/null)" \
    python3 -c "from huggingface_hub import snapshot_download; snapshot_download('AngelSlim/Hy3-GGUF', allow_patterns=['*Hy3-IQ1_M-mtp.gguf*'])"
# ---------------------------------------------------------------------

# hy3-llamacpp-spark-ai-hub — patched llama.cpp with hy_v3 (Hunyuan v3) support
#
# The hy_v3 architecture is NOT in mainline llama.cpp. AngelSlim ship two patches
# (arch + MTP/tool-parser) against a pinned base commit. This image reproduces
# their setup_hyv3_llama.sh natively for the DGX Spark (ARM64 + CUDA 13, GB10 sm_121).
#
#   base commit : 19bba67c1f4db723c60a0d421aa0788bf4ddc699 (ggml-org/llama.cpp master)
#   patch 01    : hy_v3 base architecture
#   patch 02    : MTP self-speculative decoding + thinking/tool-call parser
#
# Source patches: https://huggingface.co/AngelSlim/Hy3-GGUF/tree/main/patches

# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake ninja-build build-essential \
        libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned base commit the patches are line-anchored to. Do NOT bump casually.
ARG LLAMA_COMMIT=19bba67c1f4db723c60a0d421aa0788bf4ddc699
ARG CUDA_ARCH=121

WORKDIR /src
RUN git clone https://github.com/ggml-org/llama.cpp.git . \
    && git checkout -q ${LLAMA_COMMIT}

# Apply the two hy_v3 patches in order (arch, then MTP+parser).
COPY patches/01-hyv3-arch.patch patches/02-hyv3-mtp-tools.patch /src/_patches/
RUN git apply --check /src/_patches/01-hyv3-arch.patch \
    && git apply /src/_patches/01-hyv3-arch.patch \
    && git apply --check /src/_patches/02-hyv3-mtp-tools.patch \
    && git apply /src/_patches/02-hyv3-mtp-tools.patch

# Build server + cli with CUDA (GB10 = sm_121), curl for -hf cache resolution.
# Static ggml (default) so the CUDA driver stub links into the binaries; shared
# libs leave libggml-cuda.so with undefined cuMem* driver symbols on aarch64.
# Add the sbsa driver-stub search path so -lcuda resolves at link time (the real
# libcuda.so.1 is provided by the NVIDIA runtime at container start).
RUN cmake -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_CUDA=ON \
        -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCH} \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_CURL=ON \
        -DLLAMA_OPENSSL=OFF \
        -DCMAKE_EXE_LINKER_FLAGS="-L/usr/local/cuda-13.0/targets/sbsa-linux/lib/stubs -lcuda" \
        -DCMAKE_SHARED_LINKER_FLAGS="-L/usr/local/cuda-13.0/targets/sbsa-linux/lib/stubs -lcuda" \
    && cmake --build build --config Release -j "$(nproc)" \
        --target llama-server llama-cli llama-quantize \
    # Copy the built binaries + their shared libs (llama.cpp emits both into
    # build/bin) directly; `cmake --install` would try to install unbuilt tests.
    && mkdir -p /opt/llama/bin \
    && cp -a build/bin/. /opt/llama/bin/

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM nvcr.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
# python3 + huggingface_hub are required so the Hub's install-time prefetch
# (runs `python3 -c "...snapshot_download..."` inside this image) can cache the
# GGUF weights. hf_xet accelerates the large (~92GB) download.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 libgomp1 ca-certificates \
        python3 python3-pip \
    && pip install --no-cache-dir --break-system-packages "huggingface_hub[hf_xet]" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/llama /opt/llama
# NOTE: the chat template is NOT baked into the image. It is passed as an inline
# --chat-template value in docker-compose.yml so the full template (and the
# reasoning_effort default) is visible and editable in the Hub's compose editor.

ENV PATH="/opt/llama/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/llama/bin:${LD_LIBRARY_PATH}"

EXPOSE 8080
ENTRYPOINT ["/opt/llama/bin/llama-server"]

COPY --from=weights /weights /root/.cache/huggingface
