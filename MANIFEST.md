# Spark AI Hub — Manifest

## Core Philosophy

**One device, one purpose.** Every app in this hub exists because the NVIDIA DGX Spark can run it — beautifully, efficiently, and without compromise. Spark AI Hub is not a generic app store. It is a curated collection of AI applications designed exclusively for the DGX Spark's hardware, architecture, and constraints.

---

## Principles

### 1. Offline-First, Forever

Once you install an app, you must be able to launch it without an internet connection — **always, every time, forever.**

No silent model downloads at runtime. No missing weights discovered mid-session. No "please connect to the internet" errors after initial install. Everything an app needs to function is baked into its image during installation. Launch is instant, local, and guaranteed.

If a download fails because a server is unreachable or the connection drops, that is not something the hub works around: the install failed, and the user installs again. The hub never trades the offline-launch guarantee for download convenience.

### 2. Complete Container Isolation

Every app lives inside its own Docker container.

**Model weights are part of the app artifact.** They are baked into the image at build time, never mounted from a cache volume. An app is one thing you can point at, not an image plus a pile of downloaded state with its own lifetime. A recipe may declare volumes only for data the *user* creates — outputs, databases, uploads — never for models.

**Uninstallation is total.** Removing an app removes every byte it consumed: removing the image is sufficient. No orphaned volumes, no residual model caches, no hidden data holding space hostage. What you see is what gets freed.

This is why the cache is not merely inconvenient but forbidden: a cache outlives the thing it belongs to. Change a recipe's quantization and the old weights stay behind, unreferenced and invisible, and no amount of care at uninstall time can find what nothing points to. An image cannot drift from the app, because it *is* the app.

Corollary: never reintroduce a store that survives `docker rmi` — no HF cache volumes, no bind-mounted host caches, and no BuildKit `RUN --mount=type=cache` for weights. The last one is the tempting one, offered as a fix for re-downloading after a failed build. Refuse it.

Corollary: a build's base and intermediate images (the vLLM/llama.cpp/CUDA runtime a recipe is built `FROM`) are cache too, even though `docker rmi` on the app's own image never touches them — Docker keeps them tagged in the local store indefinitely once pulled. An image earns a permanent place on disk only by being the exact image a currently-installed recipe runs, or by backing a container that exists right now (running or stopped) — nothing else, including a still-tagged image left over from a version an app no longer uses. Install and uninstall both end by removing everything that no longer qualifies. Reinstalling costs a full re-download, every time, on purpose: that cost is the receipt proving nothing was left behind. The one exception is a base image two currently-installed recipes both still depend on — it stays, because removing it would strand a working app, not because keeping it is convenient.

### 3. DGX Spark Proven — Built Native, Not Ported

Every app in the hub is tested and verified to run on a single DGX Spark. Not "theoretically compatible" — **proven to run.**

Each setup is designed around the Spark's specific architecture and specifications: ARM64 (aarch64) CPU, NVIDIA GPU with CUDA, available VRAM, thermal envelope, and power budget. Every app is tuned to extract maximum performance from this exact hardware, as if the device existed solely to run that one app.

No x86 assumptions. No cloud-GPU presets. No configurations that waste the Spark's capabilities or exceed its limits.

**When an AI tool isn't officially supported on the Spark, we make it work anyway.** We maintain isolated, custom-built repositories with native libraries compiled specifically for the DGX Spark's ARM64 + CUDA architecture. These aren't quick hacks — they are fully adapted forks with native PyTorch CUDA builds, ARM64-compiled dependencies, and Spark-optimized configurations. When you install one of these apps, you're getting a version that was built from the ground up for this device, not an x86 binary running through translation.

### 4. One-Button Everything

Installing an app requires a single button: **Install.**
Launching an app requires a single button: **Launch.**

No terminal commands. No manual environment variables. No post-install setup wizards. Every configuration, dependency, and prerequisite is predefined and baked into the recipe. The user's only job is to click.

The same rule applies to companion tools and IDE integrations. If Spark AI Hub advertises support for a coding agent, desktop client, VS Code extension, MCP server, provider profile, or CLI wrapper, the hub-owned install or `sah` launcher must do the wiring. Users must not be asked to manually add MCP entries, hand-edit provider files, export environment variables, or launch the official client in a separate mode that bypasses the hub. External clients are only considered supported when the Hub can install, configure, select the active model for, and restore them through the same one-step experience.

Future client integrations follow the same contract: `sah <client>` is the non-invasive launcher, `sah <client> --install` makes the plain client use the Hub where a safe persistent config path exists, `sah <client> --restore` returns the client to its previous state, and `sah <client> --status` explains what is active. Persistent installs must create an exact backup before touching user configuration and must preserve unrelated user settings through a documented, schema-aware merge.

### 5. One Address for the Whole Hub

The Hub and every app it runs answer on **one port**. The UI, the API, the
OpenAI- and Anthropic-compatible endpoints, and each app at `/run/{slug}/` all
arrive on the same address.

Apps publish no host ports of their own. They share one Docker network, keep
whatever port suits them inside their container, and a proxy in front of them
routes by name. Nothing can collide, adding an app never claims a number, and
exposing the Hub to the internet — a Cloudflare Tunnel, a VPN, a reverse proxy
— means pointing one origin at one port, not tracking a port per app.

Apps have no accounts of their own, so the Hub's session guards them. Reaching
`/run/{slug}/` without signing in redirects to the sign-in page, whether the
request came over the LAN or through a tunnel.

### 6. Full Transparency

The entire Docker Compose configuration for every app is exposed in the interface. There are no hidden configurations, no secrets behind the scenes, no magic that the user cannot inspect or modify.

Users can see exactly what runs, how it runs, and change any parameter. The interface is a window into the container — not a black box.

### 7. Test the Way the User Does

All benchmarks, validation tests, and QA are performed through the Hub's interface — the same way end users will interact with the apps.

We do not test apps by SSH-ing into containers, running scripts from the host, or using developer tooling that the user never sees. If the benchmark doesn't go through the same UI flow the user experiences, it doesn't count.

### 8. Offline Validation Is Mandatory

Claiming an app works offline while the machine has internet access is not a valid test. Any verification of offline behavior must be performed with the network physically disabled or the container running with `--network none`.

The only accepted proof that an app launches offline is a successful launch in an environment where DNS resolution fails. If it hasn't been tested offline, it hasn't been tested.

---

## What This Is Not

- **Not a general-purpose container manager.** Spark AI Hub curates AI apps for one specific device.
- **Not a cloud marketplace.** There is no SaaS tier, no API backend, no telemetry. The Hub runs on your Spark and talks only to your Spark.
- **Not a development toolkit.** Apps are end-user ready. If you need to read documentation, tweak Dockerfiles, or debug startup scripts, you're using the wrong tool.

---

## The Native Build Commitment

The DGX Spark runs ARM64. Much of the AI ecosystem assumes x86. The gap between those two realities is where we spend our energy.

For every AI tool that lacks official ARM64 + CUDA support, we maintain isolated repositories with libraries compiled natively for the Spark's architecture. These are pre-built images hosted at `ghcr.io/waxacabytes/*`, each one a complete rebuild of the upstream project — PyTorch with CUDA 13 for ARM64, native diffusers, compiled dependencies — nothing translated, nothing emulated.

This applies across the hub:

- **Multimodal models** — Qwen Image, MiniCPM-o, SpatialEdit, Deep Live Cam: all rebuilt with native ARM64 CUDA libraries
- **Audio/voice tools** — Chatterbox, Voicebox, Foundation 1: native compilation, no Rosetta
- **Speculative decoding** — DFlash integration built from source into vLLM ARM64 images for accelerated inference
- **Community quantizations** — GGUF and FP8 checkpoints from the community, curated and verified to run within the Spark's VRAM budget

The custom repos are not secondary. They are first-class citizens of the hub, held to the same standards: offline-first, container-isolated, one-button install, DGX Spark proven.

---

## The Spark Commitment

An app earns its place in the hub by meeting every principle above. If it requires internet at runtime, it stays out until it doesn't. If it leaks data on uninstall, it stays out until it doesn't. If it hasn't run on real Spark hardware, it stays out until it has.

The bar is high because the device is special.
