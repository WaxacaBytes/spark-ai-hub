"""Fibo 2.0 (Bria) behind the OpenAI images API the Hub drives SGLang with.

POST /v1/images/generations  JSON  {prompt, size, seed, num_inference_steps,
                                    guidance_scale, negative_prompt}
POST /v1/images/edits        multipart: image (1-4, repeated), prompt, size,
                                    seed, num_inference_steps, guidance_scale,
                                    negative_prompt, mask (optional, white =
                                    regenerate, single image only)
GET  /v1/models, /health

Bria's BriaFibo2Pipeline (diffusers) does the work, at the settings on the
model card. The prompt is a structured VGL caption (JSON) or plain text: the
pipeline reads both, a JSON caption follows the request more closely. With
no negative prompt, the card's own negatives are used: the T2I one for
generations, the empty caption the model was trained with for edits. The
card's fonts draw the caption's text the way training drew it.
"""
import asyncio
import base64
import io
import os
import time

import torch
from diffusers import BriaFibo2Pipeline
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image

MODEL = os.environ.get("FIBO_MODEL", "/models/fibo")
SERVED = os.environ.get("FIBO_SERVED_NAME", "Fibo-2.0")
STEPS = int(os.environ.get("FIBO_STEPS", "50"))
GUIDANCE = float(os.environ.get("FIBO_GUIDANCE", "5.0"))
FONTS = [p for p in ("/models/fonts/Aileron-Regular.otf", "/models/fonts/DejaVuSans.ttf") if os.path.exists(p)]
MAX_IMAGES = 4

# The card's negative prompts (README, "Setup").
NEGATIVE_T2I = '{"short_description":"","objects":[{"description":"the main subject","texture":"smooth, featureless, plastic-like, airbrushed, no fine surface detail","appearance_details":"soft blurred edges, simplified shapes, no fine detail","skin_tone_and_texture":"waxy airbrushed skin, poreless, plastic smooth"}],"background_setting":"a plain seamless studio backdrop, perfectly smooth gradient, blurred and featureless, no visible texture","lighting":{},"aesthetics":{"artifacts":["oversaturated colours","neon colour cast","overexposed highlights","lens flare","slight blur","motion blur","haze"]},"photographic_characteristics":{"depth_of_field":"shallow, heavy bokeh obscuring most of the frame","focus":"soft, slightly out of focus, lacking fine texture"},"style_medium":"","context":"","artistic_style":""}'
NEGATIVE_EDIT = '{"short_description":"","objects":[],"background_setting":"","lighting":{},"aesthetics":{},"photographic_characteristics":{},"style_medium":"","context":"","artistic_style":""}'

app = FastAPI()
pipe = None
ready = False
lock = asyncio.Lock()


@app.on_event("startup")
async def load() -> None:
    async def _load():
        global pipe, ready
        t0 = time.time()
        pipe = await asyncio.to_thread(
            lambda: BriaFibo2Pipeline.from_pretrained(MODEL, dtype=torch.bfloat16).to("cuda"))
        ready = True
        print(f"[fibo] {SERVED} loaded in {time.time() - t0:.0f} s", flush=True)
    asyncio.create_task(_load())


@app.get("/health")
async def health():
    if not ready:
        raise HTTPException(503, "loading")
    return {"status": "ok"}


@app.get("/v1/models")
async def models():
    return {"object": "list", "data": [{"id": SERVED, "object": "model", "owned_by": "briaai"}]}


def _size(value) -> tuple[int | None, int | None]:
    """WxH rounded down to multiples of 16, as the pipeline requires."""
    if not value or value == "auto":
        return None, None
    w, h = (int(x) for x in str(value).lower().split("x"))
    return w // 16 * 16, h // 16 * 16


def _png(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()


async def _run(**kwargs) -> JSONResponse:
    if not ready:
        raise HTTPException(503, f"{SERVED} is still loading")
    seed = kwargs.pop("seed")
    generator = torch.Generator("cuda").manual_seed(int(seed)) if seed not in (None, "") else None
    async with lock:
        t0 = time.time()
        result = await asyncio.to_thread(lambda: pipe(generator=generator, fonts=FONTS or None, **kwargs))
        print(f"[fibo] {kwargs.get('num_inference_steps')} steps in {time.time() - t0:.1f} s", flush=True)
    return JSONResponse({"created": int(time.time()),
                         "data": [{"b64_json": _png(result.images[0])}]})


@app.post("/v1/images/generations")
async def generations(request: Request):
    body = await request.json()
    if not body.get("prompt"):
        raise HTTPException(400, "prompt is required")
    width, height = _size(body.get("size"))
    return await _run(prompt=body["prompt"], width=width, height=height,
                      num_inference_steps=int(body.get("num_inference_steps") or STEPS),
                      guidance_scale=float(body.get("guidance_scale") or GUIDANCE),
                      negative_prompt=body.get("negative_prompt") or (NEGATIVE_T2I if GUIDANCE > 1 else None),
                      seed=body.get("seed"))


@app.post("/v1/images/edits")
async def edits(request: Request):
    form = await request.form()
    images = [Image.open(io.BytesIO(await f.read())).convert("RGB")
              for key, f in form.multi_items() if key in ("image", "image[]") and hasattr(f, "read")]
    if not images:
        raise HTTPException(400, "at least one image is required")
    if len(images) > MAX_IMAGES:
        raise HTTPException(400, f"Fibo 2.0 takes up to {MAX_IMAGES} images, got {len(images)}")
    if not form.get("prompt"):
        raise HTTPException(400, "prompt is required")
    mask = form.get("mask")
    mask = Image.open(io.BytesIO(await mask.read())).convert("L") if hasattr(mask, "read") else None
    if mask is not None and len(images) > 1:
        raise HTTPException(400, "a mask works with one image only")
    width, height = _size(form.get("size"))
    return await _run(prompt=form["prompt"], image=images[0] if len(images) == 1 else images, mask=mask,
                      width=width, height=height,
                      num_inference_steps=int(form.get("num_inference_steps") or STEPS),
                      guidance_scale=float(form.get("guidance_scale") or GUIDANCE),
                      negative_prompt=form.get("negative_prompt") or (NEGATIVE_EDIT if GUIDANCE > 1 else None),
                      seed=form.get("seed"))
