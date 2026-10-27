"""Iris-3B Turbo (speridlabs) behind the OpenAI images API the Hub drives
SGLang with: POST /v1/images/generations {prompt, size, seed} -> b64 PNG.
GET /v1/models, /health.

The model is loaded and sampled exactly as speridlabs' scripts/sample.py does
for a Turbo export: built on the meta device from the checkpoint, FP32
weights under BF16 autocast, and generate_few_step with the four sigmas in
the export's distill.yaml. The schedule and guidance are part of the model,
so steps, guidance and negative prompts do not apply.
"""
import asyncio
import base64
import io
import os
import time
from pathlib import Path

import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from omegaconf import OmegaConf
from PIL import Image

from iris3b.config import inference_config
from iris3b.models.dit import IrisDiT
from iris3b.registry import TEXT_ENCODERS
from iris3b.sampling import generate_few_step, load_for_inference

CHECKPOINT = os.environ.get("IRIS_CHECKPOINT", "/models/iris-3b/turbo")
SERVED = os.environ.get("IRIS_SERVED_NAME", "speridlabs/iris-3b-turbo")
# The seven aspect buckets Turbo was distilled at (README); a request is
# snapped to the closest one.
BUCKETS = [(1024, 1024), (752, 1344), (1344, 752), (896, 1344), (1344, 896), (896, 1200), (1200, 896)]

app = FastAPI()
state: dict = {}
lock = asyncio.Lock()
device = torch.device("cuda")


def _load() -> None:
    t0 = time.time()
    raw, weights = load_for_inference(CHECKPOINT)
    cfg = inference_config(raw, [])
    with torch.device("meta"):
        model = IrisDiT(cfg.model)
    model.load_state_dict(weights, strict=True, assign=True)
    model = model.eval().to(device=device, dtype=torch.float32)
    import iris3b.text  # noqa: F401  (registers encoders)
    state.update(model=model, cfg=cfg,
                 text_encoder=TEXT_ENCODERS.build(cfg.text_encoder.name, cfg.text_encoder, device=device),
                 sigmas=list(OmegaConf.load(Path(CHECKPOINT, "distill.yaml")).dmd.sigmas))
    print(f"[iris] {SERVED} loaded in {time.time() - t0:.0f} s", flush=True)


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(asyncio.to_thread(_load))


@app.get("/health")
async def health():
    if "model" not in state:
        raise HTTPException(503, "loading")
    return {"status": "ok"}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": SERVED, "object": "model", "owned_by": "speridlabs"}]}


def _bucket(size) -> tuple[int, int]:
    if not size or size == "auto":
        return 1024, 1024
    w, h = (int(x) for x in str(size).lower().split("x"))
    return min(BUCKETS, key=lambda b: abs(b[0] / b[1] - w / h))


def _sample(prompt: str, width: int, height: int, seed: int | None):
    cfg = state["cfg"]
    generator = torch.Generator(device=device).manual_seed(int(seed) if seed is not None else 0)
    with torch.autocast("cuda", dtype=torch.bfloat16):
        images = generate_few_step(state["model"], state["text_encoder"], [prompt], height=height, width=width,
                                   sigmas=state["sigmas"], generator=generator, device=device,
                                   num_train_timesteps=cfg.flow.num_train_timesteps, prediction=cfg.flow.prediction)
    pixels = ((images[0].float().clamp(-1, 1) + 1) * 127.5).round().byte().permute(1, 2, 0).cpu().numpy()
    return Image.fromarray(pixels)


@app.post("/v1/images/generations")
async def generations(request: Request):
    if "model" not in state:
        raise HTTPException(503, f"{SERVED} is still loading")
    body = await request.json()
    if not body.get("prompt"):
        raise HTTPException(400, "prompt is required")
    width, height = _bucket(body.get("size"))
    async with lock:
        t0 = time.time()
        image = await asyncio.to_thread(_sample, body["prompt"], width, height, body.get("seed"))
        print(f"[iris] {width}x{height} in {time.time() - t0:.2f} s", flush=True)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return JSONResponse({"created": int(time.time()),
                         "data": [{"b64_json": base64.b64encode(out.getvalue()).decode()}]})
