# Spark AI Hub — Manifest

## Core Philosophy

**One device, one purpose.** Every app in this hub exists because the NVIDIA DGX Spark can run it — beautifully, efficiently, and without compromise. Spark AI Hub is not a generic app store. It is a curated collection of AI applications designed exclusively for the DGX Spark's hardware, architecture, and constraints.

---

## Principles

The eight principles every app must meet are in [AGENTS.md](AGENTS.md).

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

An app earns its place in the hub by meeting every principle in `AGENTS.md`. If it requires internet at runtime, it stays out until it doesn't. If it leaks data on uninstall, it stays out until it doesn't. If it hasn't run on real Spark hardware, it stays out until it has.

The bar is high because the device is special.
