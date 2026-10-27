"""OpenAI images-edit API for chinoll's MOSS Image Edit LoRA on MOSS-Image-SFT.

The sampler is the publisher's own (Chenkin-x/Moss-Image-Edit, pinned): this
file does per request what its infer.py run() does -- encode the prompt and
the reference pictures, sample with the reference-only KV cache and ordinary
CFG, decode -- but loads the text encoder, VAE, DiT and LoRA once at start
and keeps them resident, instead of loading ~28 GB for every edit. infer.py
frees each part before loading the next so it fits small GPUs; the Spark
holds them all.

The LoRA was trained with references and output at the same scale: the
publisher's workflow notes that a reference smaller than the output changes
the composition. So the output is never made larger than the first
reference as the sampler sees it (long edge MAX_EDGE): a bigger requested
size keeps its aspect ratio and is scaled down to that.
"""
import asyncio
import base64
import json
import math
import tempfile
import threading
import time
from pathlib import Path

import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

CONFIG = json.loads(Path("/app/infer.json").read_text())
ADAPTER = "/adapter"
state: dict = {}
_render_lock = asyncio.Lock()


def _dtype(name: str):
    return {"float32": torch.float32, "bfloat16": torch.bfloat16}[name]


def load() -> None:
    from infer import read_adapter_options
    import moss_edit.components as adapter
    import moss_edit.lora as lora
    import moss_edit.processing as data
    from moss_edit.model import MossEditModel

    t = time.time()
    snapshot = Path(CONFIG["snapshot_dir"]).resolve(strict=True)
    options, _ = read_adapter_options(ADAPTER)
    options["param_dtype"] = _dtype(options["param_dtype"])
    encoding = data.load_encoding_components(
        snapshot, device="cuda", dtype=_dtype(CONFIG["encoder_dtype"]),
        vae_dtype=_dtype(CONFIG["vae_dtype"]))
    model = adapter.load_transformer(snapshot, device="cuda", dtype=_dtype(CONFIG["dtype"]))
    lora.inject_lora(model, **options)
    lora.load_adapter(model, ADAPTER)
    model.requires_grad_(False).eval()
    state.update(snapshot=snapshot, encoding=encoding, model=model,
                 editor=MossEditModel(model, gradient_checkpointing=False).eval(),
                 module=adapter.load_moss_module(snapshot))
    print(f"MOSS Image Edit loaded in {time.time() - t:.0f} s", flush=True)


def output_size(first_reference: Path, width: int, height: int) -> tuple[int, int]:
    """The requested size, scaled down if needed so the output's long edge is no
    longer than the first reference's as the sampler encodes it."""
    import moss_edit.processing as data
    ref_w, ref_h = data.resize_image(str(first_reference), max_edge=CONFIG["max_edge"]).size
    scale = min(1.0, math.sqrt(ref_w * ref_h / (width * height)))
    return (max(16, round(width * scale / 16) * 16), max(16, round(height * scale / 16) * 16))


def edit_once(refs: list[str], prompt: str, negative: str, width: int, height: int,
              steps: int, cfg: float, seed: int) -> bytes:
    import moss_edit.components as adapter
    import moss_edit.processing as data
    from moss_edit.sampling import sample_latents
    from infer import _save_png

    encoding = state["encoding"]
    with torch.no_grad():
        conditions = adapter.encode_prompts(
            encoding.prompt_components, [prompt],
            negative_prompts=[negative] if cfg != 1 else None,
            max_sequence_length=CONFIG["max_sequence_length"], device="cpu")
        reference_latents = [
            data.encode_image(encoding.vae, data.image_to_tensor(
                data.resize_image(path, max_edge=CONFIG["max_edge"])), sample_posterior=False).detach().cpu()
            for path in refs]
    scheduler = state["module"].MOSSImageScheduler.from_pretrained(
        str(state["snapshot"] / "scheduler"), local_files_only=True,
        shift=CONFIG["inference_shift"], training_shift=CONFIG["training_shift"])
    latents = sample_latents(
        state["editor"], scheduler, reference_latents, conditions.positive, conditions.negative,
        height=height, width=width, steps=steps, cfg=cfg, seed=seed, device="cuda",
        latent_channels=int(state["model"].config.latent_channels), use_kv_cache=True)
    with torch.no_grad():
        image = data.decode_latents(encoding.vae, latents)
    with tempfile.TemporaryDirectory() as tmp:
        return _save_png(image, Path(tmp) / "result.png").read_bytes()


app = FastAPI(title="MOSS Image Edit")


@app.on_event("startup")
def startup() -> None:
    # Loading takes a while; /health says 503 until it is done.
    threading.Thread(target=load, daemon=True).start()


@app.get("/health")
def health():
    if "editor" not in state:
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok"}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "MOSS-Image-Edit", "object": "model"}]}


@app.post("/v1/images/edits")
async def edit(
    image: list[UploadFile] = File(...), prompt: str = Form(...),
    size: str = Form("1024x1024"), seed: int = Form(42),
    num_inference_steps: int = Form(30), guidance_scale: float = Form(4.0),
    negative_prompt: str = Form(""), n: int = Form(1),
    response_format: str = Form("b64_json"), output_format: str = Form("png"),
):
    if "editor" not in state:
        raise HTTPException(503, "Still loading the model.")
    if not 1 <= len(image) <= CONFIG["max_ref_images"] or n != 1 or response_format != "b64_json" or output_format != "png":
        raise HTTPException(400, f"Use one to {CONFIG['max_ref_images']} reference images, n=1, b64_json and PNG.")
    try:
        width, height = map(int, size.lower().split("x"))
    except (ValueError, AttributeError):
        raise HTTPException(400, "size must be WIDTHxHEIGHT") from None
    if not (256 <= width <= 2048 and 256 <= height <= 2048 and width % 16 == height % 16 == 0):
        raise HTTPException(400, "Width and height must be multiples of 16 from 256 to 2048.")
    if not 1 <= num_inference_steps <= 100 or not 0 <= seed < 2**63 or not 0 <= guidance_scale <= 20:
        raise HTTPException(400, "Invalid steps, guidance or seed.")
    async with _render_lock:
        with tempfile.TemporaryDirectory() as tmp:
            refs = []
            for i, upload in enumerate(image):
                data = await upload.read()
                if not data or len(data) > 25 * 1024 * 1024:
                    raise HTTPException(400, "Each reference image must be 1-25 MB.")
                path = Path(tmp) / f"reference-{i}.png"
                path.write_bytes(data)
                refs.append(str(path))
            width, height = output_size(Path(refs[0]), width, height)
            try:
                png = await asyncio.to_thread(edit_once, refs, prompt, negative_prompt, width, height,
                                              num_inference_steps, guidance_scale, seed)
            except (ValueError, OSError) as exc:
                raise HTTPException(400, f"MOSS edit refused the request: {exc}") from None
            finally:
                torch.cuda.empty_cache()
    return {"created": int(time.time()), "data": [{"b64_json": base64.b64encode(png).decode()}]}
