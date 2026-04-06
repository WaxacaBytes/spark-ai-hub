# Spark AI Hub

**Your AI app store for NVIDIA DGX Spark.** Browse, install, and launch AI apps with one click.

![Spark AI Hub](Spark AI Hub.png)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/WaxacaBytes/spark-ai-hub/main/install.sh | bash
```

Open **http://localhost:9000** (or `http://<your-spark-ip>:9000` from another device).

Run the same command again to update.

## What it does

- Browse a catalog of AI apps ready for DGX Spark
- Install any app with one click — no terminal needed
- Launch, stop, and monitor running apps from the dashboard
- Track GPU, RAM, disk, and temperature in real time

## Available apps

| App | What it does | GPU |
|-----|-------------|-----|
| Open WebUI + Ollama | Chat with local LLMs | Yes |
| vLLM (Qwen 3.5) | High-performance LLM inference (8 model sizes) | Yes |
| ComfyUI | Image & video generation workflows | Yes |
| FaceFusion | Face swap & enhancement | Yes |
| Hunyuan3D 2.1 | Image to 3D model generation | Yes |
| TRELLIS 2 | Text/image to 3D generation | Yes |
| LocalAI | OpenAI-compatible API server | Yes |
| AnythingLLM | RAG & AI agents | No |
| Flowise | Drag-and-drop LLM workflows | No |
| Langflow | Visual LLM app builder | No |

All apps run as Docker containers with ARM64 + CUDA support.

## Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/WaxacaBytes/spark-ai-hub/main/uninstall.sh | bash
```

Removes all Spark AI Hub containers, volumes, and files. Does not touch Docker itself.

## Requirements

- NVIDIA DGX Spark
- Docker 28+

## License

MIT
