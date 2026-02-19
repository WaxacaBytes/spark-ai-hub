# SparkForge

A Docker-native AI app launcher for NVIDIA DGX Spark. Browse a catalog of verified recipes and deploy AI workloads with one click.

## Why SparkForge?

- **Pinokio** doesn't work on DGX Spark (ARM64 incompatible)
- **NVIDIA Sync** has Custom Scripts but no catalog or community sharing
- **NVIDIA Playbooks** are great but manual — copy-paste terminal commands

SparkForge gives you a web UI to browse, install, launch, and monitor Docker-based AI apps on your Spark.

## Features

- **Recipe Catalog** — browse and search AI apps by category
- **One-Click Install** — pull and start Docker containers with a single click
- **Live Build Logs** — stream container build output via WebSocket
- **Running Apps Dashboard** — see what's running, open UIs, stop containers
- **System Monitor** — live GPU utilization, RAM, disk, and temperature charts

## Included Recipes

| Recipe | Category | Port | GPU |
|--------|----------|------|-----|
| Open WebUI + Ollama | LLM | 8080 | Yes |
| LocalAI | Multi-Modal API | 8081 | Yes |
| AnythingLLM | RAG / Agents | 3001 | No |
| Flowise | Workflow Builder | 3002 | No |
| Langflow | Visual IDE | 7860 | No |
| ComfyUI | Image / Video Gen | 8188 | Yes |

All recipes use Docker images with ARM64 support. ComfyUI uses a [custom image](https://github.com/WaxacaBytes/comfyui-spark) pre-built for DGX Spark with CUDA 13 and Blackwell support.

## Quick Start

```bash
git clone https://github.com/WaxacaBytes/sparkforge.git
cd sparkforge
./run.sh
```

Then open **http://localhost:9000** in your browser (or `http://<spark-ip>:9000` from another machine).

`run.sh` creates a Python venv, installs dependencies, and starts the server on port 9000.

## Manual Setup

```bash
# Backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn daemon.main:app --host 0.0.0.0 --port 9000

# Frontend (for development)
cd frontend
npm install
npm run dev
```

## Project Structure

```
sparkforge/
├── daemon/                 # FastAPI backend
│   ├── main.py             # App entry point
│   ├── config.py           # Settings (port, paths)
│   ├── db.py               # SQLite (installed recipes)
│   ├── routers/            # API endpoints
│   │   ├── recipes.py      # GET /api/recipes
│   │   ├── containers.py   # POST install/launch/stop, WS build logs
│   │   └── system.py       # GET /api/system/metrics, WS live metrics
│   ├── services/           # Business logic
│   │   ├── docker_service.py    # Docker compose orchestration
│   │   ├── registry_service.py  # Recipe YAML loader
│   │   └── monitor_service.py   # nvidia-smi, RAM, disk
│   └── models/             # Pydantic schemas
├── frontend/               # React + Vite + Tailwind
│   └── src/
│       ├── pages/          # Catalog, Running, System
│       ├── components/     # RecipeCard, BuildLog, SystemBar
│       └── store.js        # Zustand state management
├── registry/recipes/       # Recipe definitions
│   └── <slug>/
│       ├── recipe.yaml     # Metadata
│       └── docker-compose.yml
├── planning/               # Design docs
├── requirements.txt
└── run.sh
```

## Requirements

- NVIDIA DGX Spark (aarch64, CUDA 13)
- Python 3.12+
- Node 22+ (for frontend development only)
- Docker 28+

## License

MIT
