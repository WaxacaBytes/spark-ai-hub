"""Sol-H3 (NVIDIA's Sol-Engine for MiniMax-H3 on one DGX Spark) behind the
OpenAI videos API that SGLang Diffusion speaks, so the Spark AI Hub drives it
the way it drives every other video model:

  POST /v1/videos                 queue a job (JSON; H3's request shape:
                                  extra_params.task and extra_params.conditions,
                                  pictures as data URIs, a keyframe's
                                  frame_index 0 = first frame, -1 = last)
  GET  /v1/videos/{id}            status and progress
  GET  /v1/videos/{id}/content    the MP4, with its soundtrack

The renders themselves are Sol's own code, unchanged: runtime.pipeline.Pipeline
from NVlabs/Sana (models/minimax_h3/Sol-H3-Spark), which keeps three model
sessions resident in their own Python environments -- Qwen (prompt), Stage1
(H3 FP8 four-step VSA draft at 672x384) and Stage2 (H3 x2 latent upscaler,
H3-to-LTX adapter, LTX-2.5 three-step refine and decode) -- and returns a
1344x768, 121-frame (5.04 s at 24 fps) MP4 with H3's own audio.

A Sol pipeline serves one task family at a time (its Stage1 is built for it).
This server starts on t2va and switches to fl2va, or back, when a job asks for
the other: the switch reloads the sessions and runs Sol's full-chain warmup
on that job's inputs first (461 s measured for t2va to fl2va).

Sol's generic Stage2 prompt is a cached feature file, built once with the
INT8 Gemma and connector shipped in the image. The first start builds it into
--prompt-cache (a volume), and every later start reuses it.

One job at a time, in order: the three sessions hold most of the Spark's
memory.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import logging
import os
import secrets
import shutil
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

log = logging.getLogger("sol-server")

# Sol's fixed output: 121 frames at 24 fps, 1344x768.
SIZE, SECONDS = "1344x768", 5.04
# How long a warm request takes (NVIDIA's project page: 56 s hot E2E), for
# the progress figure; the job reports 99 until it is really done.
TYPICAL_S = 60.0
SUFFIXES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
WARMUP_PROMPT = ("integrated_multimodal_description: A red fox walks through a sunlit meadow. "
                 "A steady wide shot follows its movement. overall_soundscape: Soft wind and rustling grass. "
                 "non_diegetic_music: N/A")


class Engine:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        sys.path.insert(0, args.sol_root)
        os.chdir(args.sol_root)
        from runtime.config import load_paths, normalize_case
        from runtime.pipeline import Pipeline
        self.load_paths, self.normalize_case, self.Pipeline = load_paths, normalize_case, Pipeline
        self.pipeline = None
        self.ready = False
        self.error: str | None = None
        self.jobs: dict[str, dict] = {}
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.work = Path(args.work_dir)
        self.work.mkdir(parents=True, exist_ok=True)

    # -- startup ------------------------------------------------------------
    def build_prompt_cache(self) -> None:
        cache = Path(self.args.prompt_cache)
        if cache.is_file() and cache.stat().st_size > 0:
            log.info("prompt cache: reusing %s", cache)
            return
        cache.parent.mkdir(parents=True, exist_ok=True)
        log.info("prompt cache: building %s once (INT8 Gemma + connector)", cache)
        paths = Path(self.args.paths_dir) / "paths-t2va.json"
        env = dict(os.environ, PYTHONPATH=os.pathsep.join([self.args.offline_context, self.args.sol_root]))
        temporary = cache.with_suffix(".building")
        temporary.unlink(missing_ok=True)
        subprocess.run([self.args.stage2_python, "-m", "runtime.cache_builder", "--paths", str(paths),
                        "--output", str(temporary)], cwd=self.args.sol_root, env=env, check=True)
        temporary.replace(cache)

    def open(self, case: dict) -> None:
        """Start a pipeline for this case's task, warming it up on the case."""
        task = case["task"]
        if self.pipeline is not None:
            if self.pipeline.task == task:
                return
            self.close()
        paths = self.load_paths(Path(self.args.paths_dir) / f"paths-{task}.json", task=task)
        root = self.work / f"{task}-{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
        pipeline = self.Pipeline(paths, root, task=task)
        log.info("starting the %s sessions and Sol's warmup (%s)", task, root)
        try:
            pipeline.start(case)
        except BaseException:
            pipeline.close()
            raise
        self.pipeline = pipeline
        log.info("%s ready in %.1f s", task, pipeline.report["startup_and_warmup_s"])

    def close(self) -> None:
        pipeline, self.pipeline = self.pipeline, None
        if pipeline is not None:
            try:
                pipeline.close()
            finally:
                shutil.rmtree(pipeline.root, ignore_errors=True)

    def startup(self) -> None:
        try:
            self.build_prompt_cache()
            self.open(self.normalize_case({"case_id": "startup", "prompt": WARMUP_PROMPT, "seed": 42}))
            self.ready = True
        except BaseException as exc:
            log.exception("startup failed")
            self.error = f"{type(exc).__name__}: {exc}"
            raise

    # -- jobs ---------------------------------------------------------------
    def render(self, job: dict) -> None:
        case = self.normalize_case(job["case"], base_dir=job["dir"])
        self.open(case)
        started = time.time()
        job["render_started_at"] = started
        try:
            row = self.pipeline.generate(case)
        except BaseException:
            # A failed request takes its worker process down with it (Sol's
            # worker re-raises), so the next job starts fresh sessions.
            self.close()
            raise
        out = Path(job["dir"]) / "video.mp4"
        shutil.move(row["output"], out)
        shutil.rmtree(self.pipeline.root / case["case_id"], ignore_errors=True)
        job.update(path=str(out), e2e_s=round(row["e2e_s"], 1))

    async def worker(self) -> None:
        await asyncio.to_thread(self.startup)
        while True:
            job = self.jobs[await self.queue.get()]
            job.update(status="in_progress", progress=1, started_at=time.time())
            try:
                await asyncio.to_thread(self.render, job)
                job.update(status="completed", progress=100)
            except Exception as exc:                      # the job, not the server, failed
                log.exception("job %s failed", job["id"])
                job.update(status="failed", error={"message": str(exc)[:2000]})
            finally:
                job["completed_at"] = time.time()


def write_condition(condition: dict, folder: Path, index: int) -> str:
    uri = str(condition.get("uri") or "")
    head, _, data = uri.partition(",")
    mime = head.removeprefix("data:").split(";")[0]
    if not head.startswith("data:") or ";base64" not in head or mime not in SUFFIXES:
        raise HTTPException(400, "pictures come as base64 data URIs (PNG, JPEG or WebP)")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "a picture's data URI is not valid base64") from None
    path = folder / f"frame-{index}{SUFFIXES[mime]}"
    path.write_bytes(raw)
    return path.name


def build_app(engine: Engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker = asyncio.create_task(engine.worker())
        yield
        worker.cancel()
        engine.close()

    app = FastAPI(lifespan=lifespan)
    args = engine.args

    @app.get("/health")
    async def health():
        if engine.error:
            return JSONResponse({"status": "failed", "error": engine.error}, status_code=500)
        if not engine.ready:
            return JSONResponse({"status": "loading"}, status_code=503)
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": args.served_model_name, "object": "model",
                                            "owned_by": "MiniMax"}]}

    @app.post("/v1/videos")
    async def create(request: Request):
        fields = await request.json()
        extra = fields.get("extra_params") or {}
        prompt = str(fields.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt is required")
        task = extra.get("task") or "t2va"
        if task not in ("t2va", "fl2va"):
            raise HTTPException(400, f"this build serves t2va and fl2va, not {task!r}")
        seed = fields.get("seed")
        seed = int(seed) if seed not in (None, "") else secrets.randbelow(2**31)
        job_id = "video_" + secrets.token_hex(12)
        folder = engine.work / "jobs" / job_id
        folder.mkdir(parents=True)
        case = {"case_id": job_id, "prompt": prompt, "seed": seed, "task": task}
        for i, condition in enumerate(extra.get("conditions") or []):
            if condition.get("type") != "image" or condition.get("role") != "keyframe" \
                    or condition.get("frame_index") not in (0, -1):
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(400, "this build takes pictures pinned as the first frame "
                                         "(frame_index 0) or the last (-1), nothing else")
            key = "first_frame" if condition["frame_index"] == 0 else "last_frame"
            if key in case:
                shutil.rmtree(folder, ignore_errors=True)
                raise HTTPException(400, f"one {key.replace('_', ' ')} at most")
            case[key] = write_condition(condition, folder, i)
        if task == "fl2va" and "first_frame" not in case and "last_frame" not in case:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, "fl2va needs a first frame, a last frame or both")
        if task == "t2va" and ("first_frame" in case or "last_frame" in case):
            case["task"] = "fl2va"
        job = {"id": job_id, "object": "video", "status": "queued", "progress": 0,
               "created_at": time.time(), "size": SIZE, "seconds": SECONDS, "seed": seed,
               "case": case, "dir": str(folder)}
        engine.jobs[job_id] = job
        await engine.queue.put(job_id)
        return public(job)

    @app.get("/v1/videos/{job_id}")
    async def status(job_id: str):
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such video job")
        if job["status"] == "in_progress" and job.get("render_started_at"):
            done = (time.time() - job["render_started_at"]) / TYPICAL_S
            job["progress"] = max(job["progress"], min(99, int(5 + 94 * done)))
        return public(job)

    @app.get("/v1/videos/{job_id}/content")
    async def content(job_id: str):
        job = engine.jobs.get(job_id)
        if not job or not job.get("path"):
            raise HTTPException(404, "no such finished video")
        return FileResponse(job["path"], media_type="video/mp4")

    return app


def public(job: dict) -> dict:
    """What a client sees of a job (no server paths)."""
    out = {k: job[k] for k in ("id", "object", "status", "progress", "created_at", "size", "seconds", "seed")}
    for key in ("completed_at", "error"):
        if job.get(key) is not None:
            out[key] = job[key]
    if job.get("started_at") and job.get("completed_at"):
        out["inference_time_s"] = round(job["completed_at"] - job["started_at"], 1)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--sol-root", required=True, help="Sana's models/minimax_h3/Sol-H3-Spark")
    p.add_argument("--paths-dir", required=True, help="where paths-t2va.json and paths-fl2va.json are")
    p.add_argument("--prompt-cache", required=True, help="Sol's generic Stage2 prompt features")
    p.add_argument("--stage2-python", required=True, help="builds the prompt cache")
    p.add_argument("--offline-context", required=True, help="comfy-kitchen for the cache builder")
    p.add_argument("--work-dir", default="/tmp/sol-runs")
    p.add_argument("--served-model-name", default="MiniMaxAI/MiniMax-H3")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=30000)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # The NGC base points LD_LIBRARY_PATH at its own torch and CUDA 13.0 and
    # Triton at its CUDA 13.0 tools. Sol's Stage1 and Stage2 sessions are pip
    # torch builds with their own CUDA, as on the machine Sol was validated
    # on, so the sessions start without them; the Qwen session (NGC's own
    # torch) gets them back from its launcher, /opt/sol/qwen-python.
    for key in [k for k in os.environ if k == "LD_LIBRARY_PATH" or k.startswith("TRITON_")]:
        del os.environ[key]
    uvicorn.run(build_app(Engine(args)), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
