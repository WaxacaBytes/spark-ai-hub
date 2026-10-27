"""Hi3D v1.0 as a Spark AI Hub 3D engine.

Speaks the same meshes API as SGLang Diffusion, so the Hub's 3D tools
(generate_3d / get_3d) treat it like any other 3D model:

    POST /v1/meshes              multipart: `image` (cut out, transparent
                                 background); `seed`, `num_inference_steps`,
                                 `texture` -> a job, at once
    GET  /v1/meshes/{id}         the job: queued, in_progress, completed, failed
    GET  /v1/meshes/{id}/content the GLB
    GET  /v1/models, GET /health

Hi3D refines a coarse mesh; it does not make one. The coarse mesh comes from
Hunyuan3D-2mini Turbo, the generator Hi3D's own image_to_mesh demo uses, with
the demo's settings (30 steps, octree 256). Hi3D then refines it against the
same picture at RESOLUTION (512 = one pass, 1024 = two, 1536 = three), with
num_inference_steps scheduler updates per pass (Hi3D's default 25).

Hi3D makes geometry only. The texture is painted afterwards from the same
picture by Hunyuan3D-2's paint and delight models, as the Hunyuan3D-2mv engine
this image builds on does. Painting needs a UV layout, and xatlas's unwrap
grows faster than the face count (43 s at 200k faces, 118 s at 400k on the
Spark's CPU), so the refined mesh is first reduced to PAINT_FACES (200,000):
at that count it keeps Hi3D's surface detail, where Tencent's own 40,000
turns it faceted. texture=false returns the full refined mesh unpainted.
Jobs run one at a time.

Weights are read from MODELS (/models): the recipe bakes them into the image.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image

from hy3dgen.shapegen import FaceReducer, Hunyuan3DDiTFlowMatchingPipeline
from hy3dgen.texgen import Hunyuan3DPaintPipeline
from sparc3d import Sparc3DPipeline

log = logging.getLogger("hi3d")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")

MODEL_ID = "hi3d-v1.0"
MODELS = os.environ.get("MODELS", "/models")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/meshes")
RESOLUTION = int(os.environ.get("RESOLUTION", "1024"))
PAINT_FACES = int(os.environ.get("PAINT_FACES", "200000"))
COARSE_STEPS = 30           # Hi3D's demo coarse stage
COARSE_OCTREE = 256
MAX_JOBS = 200

models: dict = {}
jobs: dict[str, dict] = {}
queue: asyncio.Queue[str] | None = None
current: dict = {}          # the job the worker is on, for progress


def on_progress(event) -> None:
    job = current.get("job")
    if job is not None:
        done = event.pass_index * event.num_inference_steps + event.step
        job["progress"] = 15 + int(55 * done / (event.num_passes * event.num_inference_steps))


def load_models() -> None:
    """Load both stages; if that fails, exit, so the container stops and the
    Hub shows why instead of waiting on a server that will never be ready."""
    t = time.time()
    try:
        models["coarse"] = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
            "tencent/Hunyuan3D-2mini", subfolder="hunyuan3d-dit-v2-mini-turbo",
            variant="fp16", use_safetensors=True)
        models["refine"] = Sparc3DPipeline.from_pretrained(
            f"{MODELS}/hi3d-official/Hi3Dv1.0", local_files_only=True,
            dino_model_id=f"{MODELS}/facebook/dinov2-large",
            clip_model_id=f"{MODELS}/openai/clip-vit-large-patch14",
            progress_callback=on_progress).to("cuda")
        models["paint"] = Hunyuan3DPaintPipeline.from_pretrained(
            "tencent/Hunyuan3D-2", subfolder="hunyuan3d-paint-v2-0")
    except Exception:  # noqa: BLE001 - logged, then the process ends
        log.exception("loading the models failed")
        os._exit(1)
    log.info("models loaded in %.0f s", time.time() - t)


def build(job: dict) -> None:
    """One job, start to finish, on the worker thread."""
    image = job.pop("image")
    started = time.time()
    job["status"], job["progress"] = "in_progress", 5
    coarse = models["coarse"](image=image, num_inference_steps=COARSE_STEPS,
                              octree_resolution=COARSE_OCTREE,
                              generator=torch.manual_seed(job["seed"]), output_type="trimesh")[0]
    job["progress"], job["coarse_s"] = 15, round(time.time() - started, 2)
    current["job"] = job
    try:
        result = models["refine"](image, coarse, resolution=RESOLUTION,
                                  num_inference_steps=job["steps"], seed=job["seed"])
    finally:
        current.pop("job", None)
    mesh = result.mesh
    job["refine_s"] = round(time.time() - started - job["coarse_s"], 2)
    if job["texture"]:
        job["progress"] = 70
        painting = time.time()
        mesh = models["paint"](FaceReducer()(mesh, max_facenum=PAINT_FACES), image=image)
        job["paint_s"] = round(time.time() - painting, 2)
    path = os.path.join(OUTPUT_DIR, f"{job['id']}.glb")
    mesh.export(path)
    job.update(status="completed", progress=100, completed_at=int(time.time()),
               inference_time_s=round(time.time() - started, 2), file_path=path,
               file_size_bytes=os.path.getsize(path), resolution=result.resolution,
               faces=len(mesh.faces))
    torch.cuda.empty_cache()


async def worker() -> None:
    while True:
        job = jobs.get(await queue.get())
        if job is None:
            continue
        try:
            await asyncio.to_thread(build, job)
        except Exception as exc:  # noqa: BLE001 - reported to the caller
            log.exception("job %s failed", job["id"])
            job.update(status="failed", error={"message": f"{type(exc).__name__}: {exc}"})
            torch.cuda.empty_cache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    queue = asyncio.Queue()
    # Loading takes a while; /health says 503 until it is done.
    threading.Thread(target=load_models, daemon=True).start()
    task = asyncio.create_task(worker())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)


def public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("image", "file_path", "steps", "texture")} | {"object": "mesh"}


@app.post("/v1/meshes")
async def create_mesh(
    image: UploadFile = File(...),
    seed: int | None = Form(None),
    num_inference_steps: int = Form(25),
    texture: bool = Form(True),
    output_format: str = Form("glb"),
):
    if not models.get("paint"):
        raise HTTPException(503, "Still loading the models.")
    if output_format.lower() != "glb":
        raise HTTPException(422, "Only GLB output is offered.")
    try:
        picture = Image.open(io.BytesIO(await image.read()))
        picture.load()
    except Exception:  # noqa: BLE001 - PIL raises many types
        raise HTTPException(400, "The image is not a readable picture.") from None
    picture = picture.convert("RGBA")
    if picture.getchannel("A").getextrema()[0] == 255:
        raise HTTPException(422, "The image has no transparent background: cut the object "
                                 "out first (the Spark AI Hub does this for you).")
    while len(jobs) >= MAX_JOBS:
        jobs.pop(next(iter(jobs)))
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "id": job_id, "model": MODEL_ID, "status": "queued", "progress": 0,
        "created_at": int(time.time()), "format": "glb", "image": picture,
        "seed": seed if seed is not None else int(time.time()) % 2**31,
        "steps": max(1, min(num_inference_steps, 100)), "texture": texture,
    }
    await queue.put(job_id)
    return public(jobs[job_id])


@app.get("/v1/meshes/{job_id}")
async def get_mesh(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Mesh not found")
    return public(jobs[job_id])


@app.get("/v1/meshes/{job_id}/content")
async def mesh_content(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Mesh not found")
    if job["status"] != "completed":
        raise HTTPException(404, "Generation is still in progress")
    return FileResponse(job["file_path"], media_type="model/gltf-binary", filename=f"{job_id}.glb")


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "hi3d-official"}]}


@app.get("/health")
async def health():
    if not models.get("paint"):
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "30000")))
