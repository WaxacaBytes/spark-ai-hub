"""Idol as a Spark AI Hub 3D engine.

Speaks the same meshes API as SGLang Diffusion, so the Hub's 3D tools
(generate_3d / get_3d) treat it like any other 3D model -- except that what
it makes is a Gaussian splat, not a mesh:

    POST /v1/meshes              multipart: `image`; `seed`,
                                 `num_inference_steps` (10-100), `mode`
                                 (synthetic | real), `gaussians`
                                 (32,768-1,048,576) -> a job, at once
    GET  /v1/meshes/{id}         the job: queued, in_progress, completed, failed
    GET  /v1/meshes/{id}/content the splat, a standard 3DGS PLY
    GET  /v1/models, GET /health

One picture of one object, with or without a background: Idol cuts the
object out itself with BiRefNet when the picture has no transparency, crops
a square around it (tight for a render or a generated picture, `mode`
synthetic; looser for a photo, `mode` real), and generates the Gaussians,
including the sides the picture never showed. Idol also predicts the camera
the picture was taken from; the splat is turned about its up axis so that
side faces the front (+Z, where glTF and the Hub's viewer put a model's
front), and saved as a standard 3DGS PLY. The models stay loaded between
jobs; jobs run one at a time.

Everything below is speridlabs' own idol-sdk (IdolPipeline): its loading, its
preprocessing and its sampler defaults. Its faster sampling (torch.compile of
the denoiser) compiles on the first picture a process sees, so the server
makes one small throwaway splat while it loads: /health turns ok only after,
and the first real job runs at full speed.
"""
from __future__ import annotations

import asyncio
import io
import logging
import math
import os
import threading
import time
import uuid
from contextlib import asynccontextmanager

import torch
import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageDraw

log = logging.getLogger("idol")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")

MODEL_ID = "idol"
WEIGHTS = os.environ.get("IDOL_WEIGHTS", "/models/speridlabs/idol")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "/tmp/splats")
MODES = ("synthetic", "real")
MIN_GAUSSIANS, MAX_GAUSSIANS = 32_768, 1_048_576
MAX_JOBS = 200

models: dict = {}
jobs: dict[str, dict] = {}
queue: asyncio.Queue[str] | None = None


def warm_up(pipeline) -> None:
    """One small splat of a grey ball on a transparent picture, so
    torch.compile has compiled the denoiser before the first real job. The
    sampled shapes do not depend on the step count or the number of
    Gaussians, so two steps and the fewest Gaussians compile all of it."""
    ball = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(ball).ellipse((256, 256, 768, 768), fill=(128, 128, 128, 255))
    pipeline(ball, steps=2, num_gaussians=MIN_GAUSSIANS)


def load_models() -> None:
    """Load Idol and compile it; if that fails, exit, so the container stops
    and the Hub shows why instead of waiting forever."""
    t = time.time()
    try:
        from idol import IdolPipeline
        models["pipeline"] = IdolPipeline(WEIGHTS)
        log.info("models loaded in %.0f s; compiling", time.time() - t)
        warm_up(models["pipeline"])
        torch.cuda.empty_cache()
        models["ready"] = True
    except Exception:  # noqa: BLE001 - logged, then the process ends
        log.exception("loading the models failed")
        os._exit(1)
    log.info("ready in %.0f s", time.time() - t)


def face_front(gaussian, yaw: float) -> None:
    """Turn the Gaussians (Z-up, as Idol makes them) about Z so the camera
    Idol predicted for the picture -- `yaw` from +Y towards +X -- looks from
    -Y, which the PLY's Y-up viewers show as the front (+Z). Pitch is left
    alone, so the object stays upright."""
    theta = yaw - math.pi
    c, s = math.cos(theta), math.sin(theta)
    turn = torch.tensor([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]],
                        device=gaussian.xyz.device, dtype=gaussian.xyz.dtype)
    gaussian.xyz = gaussian.xyz @ turn.T
    # The same turn as a (w, x, y, z) quaternion, applied before each Gaussian's own.
    w, z = math.cos(theta / 2), math.sin(theta / 2)
    q = gaussian.rotation
    gaussian.rotation = torch.stack([w * q[:, 0] - z * q[:, 3], w * q[:, 1] - z * q[:, 2],
                                     w * q[:, 2] + z * q[:, 1], w * q[:, 3] + z * q[:, 0]], dim=1)


def build(job: dict) -> None:
    """One job, start to finish, on the worker thread."""
    image = job.pop("image")
    started = time.time()
    job["status"], job["progress"] = "in_progress", 5
    output = models["pipeline"](image, mode=job["mode"], seed=job["seed"], steps=job["steps"],
                                num_gaussians=job["gaussians"])
    job["progress"] = 90
    job["camera"] = {k: round(v, 4) for k, v in output.camera.items()}
    face_front(output.gaussian, output.camera["yaw"])
    path = os.path.join(OUTPUT_DIR, f"{job['id']}.ply")
    output.gaussian.save_ply(path)
    job.update(status="completed", progress=100, completed_at=int(time.time()),
               inference_time_s=round(time.time() - started, 2), file_path=path,
               file_size_bytes=os.path.getsize(path))
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
            job.pop("image", None)
            job.update(status="failed", error={"message": f"{type(exc).__name__}: {exc}"})
            torch.cuda.empty_cache()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global queue
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    queue = asyncio.Queue()
    threading.Thread(target=load_models, daemon=True).start()   # /health is 503 until done
    task = asyncio.create_task(worker())
    yield
    task.cancel()


app = FastAPI(lifespan=lifespan)

HIDDEN = ("image", "file_path")


def public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in HIDDEN} | {"object": "mesh"}


@app.post("/v1/meshes")
async def create_mesh(
    image: UploadFile = File(...),
    seed: int | None = Form(None),
    # idol-sdk's own defaults: 50 steps, a render's crop, 262,144 Gaussians.
    num_inference_steps: int = Form(50),
    mode: str = Form("synthetic"),
    gaussians: int = Form(262_144),
    output_format: str = Form("ply"),
):
    if not models.get("ready"):
        raise HTTPException(503, "Still loading the models.")
    if output_format.lower() != "ply":
        raise HTTPException(422, "Idol makes Gaussian splats: output_format is ply.")
    if mode not in MODES:
        raise HTTPException(422, f"mode is one of {MODES}.")
    if not 10 <= num_inference_steps <= 100:
        raise HTTPException(422, "num_inference_steps is 10 to 100.")
    if not MIN_GAUSSIANS <= gaussians <= MAX_GAUSSIANS:
        raise HTTPException(422, f"gaussians is {MIN_GAUSSIANS:,} to {MAX_GAUSSIANS:,}.")
    try:
        picture = Image.open(io.BytesIO(await image.read()))
        picture.load()
    except Exception:  # noqa: BLE001 - PIL raises many types
        raise HTTPException(400, f"{image.filename or 'The picture'} is not a readable image.") from None
    while len(jobs) >= MAX_JOBS:
        jobs.pop(next(iter(jobs)))
    job_id = uuid.uuid4().hex
    jobs[job_id] = {
        "id": job_id, "model": MODEL_ID, "status": "queued", "progress": 0,
        "created_at": int(time.time()), "format": "ply", "image": picture,
        "seed": seed if seed is not None else int(time.time()) % 2**31,
        "steps": num_inference_steps, "mode": mode,
        "gaussians": gaussians // 32 * 32,
    }
    await queue.put(job_id)
    return public(jobs[job_id])


@app.get("/v1/meshes/{job_id}")
async def get_mesh(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Splat not found")
    return public(jobs[job_id])


@app.get("/v1/meshes/{job_id}/content")
async def mesh_content(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Splat not found")
    if job["status"] != "completed":
        raise HTTPException(404, "Generation is still in progress")
    return FileResponse(job["file_path"], media_type="application/x-ply", filename=f"{job_id}.ply")


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "speridlabs"}]}


@app.get("/health")
async def health():
    if not models.get("ready"):
        return JSONResponse({"status": "loading"}, status_code=503)
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "30000")))
