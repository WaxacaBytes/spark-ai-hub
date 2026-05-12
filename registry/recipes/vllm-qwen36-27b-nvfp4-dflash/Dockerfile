FROM vllm/vllm-openai:v0.20.0-aarch64-cu130

RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir git+https://github.com/z-lab/dflash.git \
 && pip cache purge || true
