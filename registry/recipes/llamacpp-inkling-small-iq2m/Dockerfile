# inkling-llamacpp-spark-ai-hub — llama.cpp with the TML Inkling architecture
#
# Inkling is NOT in mainline llama.cpp yet. Support lives in an open upstream PR:
#
#   PR     : https://github.com/ggml-org/llama.cpp/pull/25731  ("Add TML Inkling architecture")
#   author : danielhanchen (Unsloth) — the same person who produced the GGUFs
#   branch : https://github.com/danielhanchen/llama.cpp  @ add-inkling
#
# The PR adds the arch/graph build, a banded flash-attention kernel, and int64
# indexing fixes needed by the 256-expert MoE. We build that branch natively for
# the DGX Spark (ARM64 + CUDA 13, GB10 sm_121) instead of carrying patch files,
# because the change set spans 62 files and is still moving upstream.
#
# The commit is PINNED. Bumping it means re-verifying that the model still loads
# and still produces coherent output — do not bump casually.

# ── Stage 1: build ───────────────────────────────────────────────────────────
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git cmake ninja-build build-essential \
        libcurl4-openssl-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG LLAMA_REPO=https://github.com/danielhanchen/llama.cpp.git
ARG LLAMA_COMMIT=d69b7e606af7f59d9e81c5c81a5c7ce0dad197e1
ARG CUDA_ARCH=121

WORKDIR /src
RUN git clone ${LLAMA_REPO} . \
    && git checkout -q ${LLAMA_COMMIT}

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
        --target llama-server llama-cli \
    # Copy the built binaries + their shared libs (llama.cpp emits both into
    # build/bin) directly; `cmake --install` would try to install unbuilt tests.
    && mkdir -p /opt/llama/bin \
    && cp -a build/bin/. /opt/llama/bin/

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM nvcr.io/nvidia/cuda:13.0.1-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive
# python3 + huggingface_hub are required so the Hub's install-time prefetch
# (runs `python3 -c "...snapshot_download..."` inside this image) can cache the
# GGUF weights. hf_xet accelerates the large (~82GB) download.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcurl4 libgomp1 ca-certificates \
        python3 python3-pip \
    && pip install --no-cache-dir --break-system-packages "huggingface_hub[hf_xet]" \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/llama /opt/llama

ENV PATH="/opt/llama/bin:${PATH}" \
    LD_LIBRARY_PATH="/opt/llama/bin:${LD_LIBRARY_PATH}"

EXPOSE 8080
ENTRYPOINT ["/opt/llama/bin/llama-server"]
