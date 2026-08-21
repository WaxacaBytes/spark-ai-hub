# Spark AI Hub

**Your AI app store for NVIDIA DGX Spark.** Browse, install, and launch AI apps with one click.

![Spark AI Hub](Spark AI Hub.png)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/WaxacaBytes/spark-ai-hub/main/install.sh | bash
```

Open **http://localhost:9000** (or `http://<your-spark-ip>:9000` from another device).

The first person to open the Hub creates the administrator account — do this
before exposing the Hub to anything beyond your own machine.

Run the same command again to update.

## What it does

- Browse a catalog of AI apps ready for DGX Spark
- Install any app with one click — no terminal needed
- Launch, stop, and monitor running apps from the dashboard
- Track GPU, RAM, disk, and temperature in real time

## Accounts

The Hub requires a sign-in, so it is safe to put behind a Cloudflare Tunnel or
a reverse proxy.

- **The first account is the administrator.** Whoever opens a fresh Hub first
  claims it — there is no default password to change.
- **Everyone after that waits for approval.** They can sign up, but they cannot
  sign in until an admin approves them from **Users**. An admin can also create
  accounts directly with an email and password.
- **Approved users can do everything** — install, launch, stop, edit compose.
  The **Users** page, where access is granted and usage is reported, is the one
  admin-only screen.
- **Everyone can change their own email and password** from **Account**.

### API keys

Each account gets one API key covering every model the Hub serves — the running
model can change underneath it and the key keeps working. Find it under
**Account**, or let the **Connect a device** panel hand you the ready-made
installer line:

```bash
curl -fsSL http://<your-spark>:9000/sah/install.sh | sh -s -- --key sah-xxxxxxxx
```

An existing `sah` install picks up a key with `sah set-key <key>`, and
`$SAH_API_KEY` overrides it for one-off shells. Every model call is attributed
to the key that made it, which is what the admin usage figures report.

To run without any of this — a Hub on a network you are certain is private —
start it with `SPARK_AI_HUB_AUTH_ENABLED=false`.

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
