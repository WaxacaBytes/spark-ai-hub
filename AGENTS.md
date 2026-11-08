# Agent Instructions

## Principles

These bind every change to the Hub and its recipes. `MANIFEST.md` says what the Hub is; the rules live here.

### 1. Offline-First, Forever

Once you install an app, you must be able to launch it without an internet connection — **always, every time, forever.**

No silent model downloads at runtime. No missing weights discovered mid-session. No "please connect to the internet" errors after initial install. Everything an app needs to function is baked into its image during installation. Launch is instant, local, and guaranteed.

If a download fails because a server is unreachable or the connection drops, that is not something the hub works around: the install failed, and the user installs again. The hub never trades the offline-launch guarantee for download convenience.

### 2. Complete Container Isolation

Every app lives inside its own Docker container.

**Model weights are part of the app artifact.** They are baked into the image at build time, never mounted from a cache volume. An app is one thing you can point at, not an image plus a pile of downloaded state with its own lifetime. A recipe may declare volumes only for data the *user* creates — outputs, databases, uploads — never for models, and never for a cache of any kind. vLLM, SGLang, Triton and FlashInfer JIT, compile and autotune caches and `PYTHONPYCACHEPREFIX` all count, however small the volume and even if it is removed on uninstall. Boot-time work that needs the GPU cannot be baked at build time, so it stays per-boot; speed it up only with stateless flags or content baked into the image.

**Uninstallation is total.** Removing an app removes every byte it consumed: removing the image is sufficient. No orphaned volumes, no residual model caches, no hidden data holding space hostage. What you see is what gets freed.

This is why the cache is not merely inconvenient but forbidden: a cache outlives the thing it belongs to. Change a recipe's quantization and the old weights stay behind, unreferenced and invisible, and no amount of care at uninstall time can find what nothing points to. An image cannot drift from the app, because it *is* the app.

Corollary: never reintroduce a store that survives `docker rmi` — no HF cache volumes, no bind-mounted host caches, and no BuildKit `RUN --mount=type=cache` for weights. The last one is the tempting one, offered as a fix for re-downloading after a failed build. Refuse it.

Corollary: a build's base and intermediate images (the vLLM/llama.cpp/CUDA runtime a recipe is built `FROM`) are cache too, even though `docker rmi` on the app's own image never touches them — Docker keeps them tagged in the local store indefinitely once pulled. An image earns a permanent place on disk only by being the exact image a currently-installed recipe runs, or by backing a container that exists right now (running or stopped) — nothing else, including a still-tagged image left over from a version an app no longer uses. Install and uninstall both end by removing everything that no longer qualifies. Reinstalling costs a full re-download, every time, on purpose: that cost is the receipt proving nothing was left behind. The one exception is a base image two currently-installed recipes both still depend on — it stays, because removing it would strand a working app, not because keeping it is convenient.

### 3. DGX Spark Proven — Built Native, Not Ported

Every app in the hub is tested and verified to run on a single DGX Spark. Not "theoretically compatible" — **proven to run.**

Each setup is designed around the Spark's specific architecture and specifications: ARM64 (aarch64) CPU, NVIDIA GPU with CUDA, available VRAM, thermal envelope, and power budget. Every app is tuned to extract maximum performance from this exact hardware, as if the device existed solely to run that one app.

No x86 assumptions. No cloud-GPU presets. No configurations that waste the Spark's capabilities or exceed its limits.

**If it is not runnable out of the box, ask first.** Before proposing a recipe, check that a stock published arm64 image supports it. If only a custom build would work, say so and ask before starting one. A model too new for any published image usually gets one soon, so re-check the registry rather than building. The custom-built images already in the hub stay and are held to these same principles; this rule is about adding new ones.

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

All benchmarks, validation tests, and QA go through the Hub — the same path end users take.

The daemon API is that path, because it is what the UI calls. Drive the whole flow yourself through it: install, launch, verify. Never hand the clicking back to the user. The test is done when the app is visibly running in the Hub.

We do not test apps by SSH-ing or exec-ing into containers, by a bare `docker compose up`, or with helper containers outside the Hub. If a benchmark doesn't go through the Hub, it doesn't count.

### 8. Offline Validation Is Mandatory

Claiming an app works offline while the machine has internet access is not a valid test. Any verification of offline behavior must be performed with the network physically disabled or the container running with `--network none`.

The only accepted proof that an app launches offline is a successful launch in an environment where DNS resolution fails. If it hasn't been tested offline, it hasn't been tested.

## User-Visible Hub Changes

When changing anything that affects the Spark AI Hub interface, catalog, recipes, recipe metadata, installed/running app state, Docker Compose behavior, or API-visible data, do not call the work finished until the running Hub reflects the change.

Required completion checklist:

- Restart the Hub backend when recipe/catalog/interface state may be cached or stale.
- Verify the backend responds after restart, normally with `GET http://127.0.0.1:9000/api/recipes`.
- Verify expected recipes/apps are present or removed through the API.
- Verify no unintended model/app container is left running (LLMs share memory, not a port: `GET /v1/models` lists every one that is up).
- State in the final response whether the server was restarted and what was verified.

This is mandatory for Hub-facing work. The user expects to open the interface immediately after a task and see the completed changes.
