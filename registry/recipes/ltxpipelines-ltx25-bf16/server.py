"""LTX-2.5 on Lightricks' own pipelines (ltx-pipelines), behind the OpenAI
videos API that SGLang Diffusion speaks, so the Spark AI Hub drives it the way
it drives every other video model:

  POST /v1/videos                 queue a job (JSON, or multipart with
                                  `input_reference` / `input_references`
                                  files: a picture pins the first frame, a
                                  clip is an IC-LoRA's reference)
  GET  /v1/videos/{id}            status and progress
  GET  /v1/videos/{id}/content    the MP4, with its soundtrack

and SGLang's LoRA endpoints, so the Hub's lora_service switches adapters here
exactly as it does on SGLang:

  POST /v1/set_lora               {lora_nickname, lora_path, strength,
                                   pipeline?, text_embeddings_path?}
  POST /v1/unmerge_lora_weights   back to the bare checkpoint
  GET  /v1/list_loras             which adapter is on

Which of Lightricks' pipelines renders a job follows from the adapter that is
on, the way their own CLIs pair them:

  no LoRA or a plain LoRA   DistilledPipeline: half size, 2x latent upsample,
                            short full-size refine (text or a first frame)
  an IC-LoRA                ICLoraPipeline, same two stages, the clip as
                            in-context reference, a picture its first frame
  pipeline "ic"             an IC-LoRA whose file does not say so (Layout to
                            Render ships without reference_downscale_factor)
  pipeline "ic-one-stage"   an IC-LoRA in one stage at the full size, as
                            Lightricks' single-stage workflow runs the 2x
                            Pixel Spatial Upscaler
  pipeline "alpha-gen"      AlphaGenPipeline: one guided stage on the full
                            (dev) model, video CFG 1, STG off
  pipeline "hdr"            HDRICLoraPipeline: one stage, the LoRA's own text
                            embedding, a 10-bit BT.2020 HLG master
  pipeline "slow-motion"    DistilledPipeline with the motion sampled at
                            fps / speed and the file played at fps

One job at a time, in order: the GPU holds one 22B transformer.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from safetensors import safe_open

import av

from ltx_core.loader import LTXV_LORA_COMFY_RENAMING_MAP, LoraPathStrengthAndSDOps
from ltx_core.model.video_vae import get_video_chunks_number
from ltx_pipelines.alpha_gen import NEGATIVE_PROMPT as ALPHA_NEGATIVE_PROMPT
from ltx_pipelines.alpha_gen import AlphaGenPipeline, alpha_gen_guider_params
from ltx_pipelines.distilled import DistilledPipeline
from ltx_pipelines.hdr_ic_lora import HDRICLoraPipeline
from ltx_pipelines.ic_lora import ICLoraPipeline
from ltx_pipelines.utils.constants import detect_params
from ltx_pipelines.utils.media_io import EXRColorSpace, VideoInput, encode_video
from ltx_pipelines.utils.model_paths import ModelPaths
from ltx_pipelines.utils.types import ImageConditioningInput

log = logging.getLogger("ltx-server")

VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".avi")

# How far along a render is, read off the pipelines' own log lines (they
# report no progress otherwise): the share of a typical render done when each
# phase starts.
PHASES = (
    ("Prompt encoding complete", 10),
    ("Running denoising loop (8 steps", 15),
    ("Building video encoder + spatial upsampler", 45),
    ("Running denoising loop (3 steps", 50),
    ("Building video decoder", 75),
    ("Building audio decoder", 95),
)


class ProgressFromLog(logging.Handler):
    """Moves the running job's progress forward as the pipeline logs each phase."""

    def __init__(self, engine: "Engine") -> None:
        super().__init__(logging.INFO)
        self.engine = engine

    def emit(self, record: logging.LogRecord) -> None:
        job = self.engine.running
        if job is None:
            return
        message = record.getMessage()
        for marker, percent in PHASES:
            if message.startswith(marker) and percent > job["progress"]:
                job["progress"] = percent


def frames_for(seconds: float, fps: int) -> int:
    """The 8k+1 frame count LTX's VAE needs, at or just above `seconds`."""
    return max(9, round(seconds * fps) // 8 * 8 + 1)


def is_ic_lora(path: str) -> bool:
    """IC-LoRAs record how their reference clip was scaled in training;
    plain LoRAs do not."""
    with safe_open(path, framework="pt") as f:
        return "reference_downscale_factor" in (f.metadata() or {})


def clip_of_frames(clip: str, limit: int) -> str:
    """The first 8k+1 frames of `clip` (at most `limit`), re-encoded: the HDR
    pipeline keeps the source's length and needs that count."""
    out = str(Path(clip).with_suffix(".cut.mp4"))
    with av.open(clip) as src:
        stream_in = src.streams.video[0]
        frames = [f.to_ndarray(format="rgb24") for f in src.decode(stream_in)]
        rate = stream_in.average_rate or 24
    keep = max(9, (min(len(frames), limit) - 1) // 8 * 8 + 1)
    if keep > len(frames):
        raise ValueError(f"the clip has {len(frames)} frames; HDR needs at least 9")
    height, width = frames[0].shape[0] // 2 * 2, frames[0].shape[1] // 2 * 2
    with av.open(out, "w") as dst:
        stream = dst.add_stream("libx264", rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        stream.options = {"crf": "12"}
        for image in frames[:keep]:
            for packet in stream.encode(av.VideoFrame.from_ndarray(image[:height, :width], format="rgb24")):
                dst.mux(packet)
        for packet in stream.encode():
            dst.mux(packet)
    return out


class Engine:
    """The pipelines, the adapter that is on, and the job queue."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.paths = ModelPaths.from_split(
            transformer_path=args.transformer_path,
            text_encoder_path=args.text_encoder_path,
            video_vae_path=args.video_vae_path,
            audio_vae_path=args.audio_vae_path,
            duration_head_path=args.duration_head_path,
        )
        self.lora: dict | None = None            # {nickname, path, strength, ic}
        self._pipeline = None
        self._pipeline_key = None
        self.jobs: dict[str, dict] = {}
        self.running: dict | None = None
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.out_dir = Path(args.output_path)
        self.out_dir.mkdir(parents=True, exist_ok=True)

    # -- pipelines ---------------------------------------------------------

    def _paths(self, *, full: bool = False) -> ModelPaths:
        if not full:
            return self.paths
        if not self.args.full_transformer_path:
            raise ValueError("this engine was started without the full (dev) transformer")
        return ModelPaths.from_split(
            transformer_path=self.args.full_transformer_path,
            text_encoder_path=self.args.text_encoder_path,
            video_vae_path=self.args.video_vae_path,
            audio_vae_path=self.args.audio_vae_path,
            duration_head_path=self.args.duration_head_path,
        )

    def pipeline(self, kind: str):
        """The pipeline for this job, built again only when the adapter or the
        kind of job changed. The transformer itself is built per render inside
        the pipeline (Lightricks' design); this keeps the rest."""
        lora = self.lora
        key = (kind, lora and lora["path"], lora and lora["strength"])
        if key == self._pipeline_key:
            return self._pipeline
        self._pipeline = None
        torch.cuda.empty_cache()
        loras = ([LoraPathStrengthAndSDOps(lora["path"], lora["strength"], LTXV_LORA_COMFY_RENAMING_MAP)]
                 if lora else [])
        upsampler = self.args.spatial_upsampler_path
        if kind == "ic":
            self._pipeline = ICLoraPipeline(model_paths=self.paths, spatial_upsampler_path=upsampler, loras=loras)
        elif kind == "alpha-gen":
            self._pipeline = AlphaGenPipeline(model_paths=self._paths(full=True), loras=loras)
        elif kind == "hdr":
            self._pipeline = HDRICLoraPipeline(model_paths=self.paths, hdr_lora=lora["path"],
                                               text_embeddings_path=lora["text_embeddings_path"])
        else:
            self._pipeline = DistilledPipeline(model_paths=self.paths, spatial_upsampler_path=upsampler,
                                               loras=loras)
        self._pipeline_key = key
        return self._pipeline

    def kind_of(self, job: dict) -> str:
        """Which pipeline renders `job`, checked against what it was given."""
        lora = self.lora or {}
        named = lora.get("pipeline")
        has_clip = bool(job.get("video_path"))
        if named in ("alpha-gen", "hdr"):
            if not has_clip:
                raise ValueError(f"{lora['nickname']} works on a clip: pass one as video.")
            return named
        if lora.get("ic"):
            if not has_clip:
                raise ValueError(f"{lora['nickname']} is an IC-LoRA: it edits a clip, so pass one as video.")
            return "ic"
        if has_clip:
            raise ValueError("A clip is edited through an IC-LoRA (Day-to-Night, Colorization, ...): "
                             "load one first. This model does not take a clip otherwise.")
        return named or "distilled"

    @torch.inference_mode()
    def render(self, job: dict) -> None:
        kind = self.kind_of(job)
        clip, picture = job.get("video_path"), job.get("image_path")
        path = self.out_dir / f"{job['id']}.mp4"
        pipe = self.pipeline(kind)
        fps = job["fps"]
        if kind == "hdr":
            source = clip_of_frames(clip, job["num_frames"])
            job["temp"].append(source)
            hdr, source_fps = pipe(video=VideoInput(Path(source), gamma_encoded=True), seed=job["seed"],
                                   keyframe_strength=0.95)
            # The HLG master; encode_video writes an EXR sidecar beside it,
            # which this server does not serve.
            encode_video(video=hdr, fps=round(source_fps), audio=None, output_path=str(path),
                         video_chunks_number=1, color_space=EXRColorSpace.ACESCG)
            for sidecar in self.out_dir.glob(f"{job['id']}_*exr*"):
                shutil.rmtree(sidecar, ignore_errors=True)
            job["path"] = str(path)
            return
        # A picture pins the first frame.
        images = [ImageConditioningInput(picture, 0, job["image_strength"])] if picture else []
        common = dict(prompt=job["prompt"], seed=job["seed"], height=job["height"], width=job["width"],
                      num_frames=job["num_frames"], images=images)
        audio_wanted = True
        if kind == "ic":
            # One full-size stage: the pipeline's first stage is half the size
            # it is asked for, so it is asked for twice the size with the
            # second stage skipped. (Two stages would also shrink the
            # upscaler's half-size reference to a quarter, too small for the
            # VAE to encode.)
            single = self.lora["one_stage"]
            if single:
                common.update(height=job["height"] * 2, width=job["width"] * 2)
            out = pipe(**common, frame_rate=fps, video_conditioning=[(clip, 1.0)],
                       conditioning_attention_strength=job["conditioning_strength"], skip_stage_2=single)
        elif kind == "alpha-gen":
            params = detect_params(self.args.full_transformer_path)
            out = pipe(**common, negative_prompt=ALPHA_NEGATIVE_PROMPT, frame_rate=fps,
                       num_inference_steps=params.num_inference_steps,
                       video_guider_params=alpha_gen_guider_params(params.video_guider_params, cfg_scale=1.0),
                       video_conditioning=[(clip, 1.0)])
        elif kind == "slow-motion":
            # The card's knob: motion sampled as if at fps / speed, the file
            # played at fps. The soundtrack would run at the sampled rate, so
            # a slowed clip carries none.
            speed = job["speed"]
            out = pipe(**common, frame_rate=fps / speed)
            audio_wanted = speed == 1.0
        else:
            out = pipe(**common, frame_rate=fps)
        encode_video(video=out.video, fps=fps, audio=out.audio if audio_wanted else None,
                     output_path=str(path),
                     video_chunks_number=get_video_chunks_number(out.num_frames, out.tiling_config))
        job["path"] = str(path)

    async def worker(self) -> None:
        while True:
            job = self.jobs[await self.queue.get()]
            job.update(status="in_progress", progress=1, started_at=time.time())
            self.running = job
            try:
                await asyncio.to_thread(self.render, job)
                job.update(status="completed", progress=100)
            except Exception as exc:                      # the job, not the server, failed
                log.exception("job %s failed", job["id"])
                job.update(status="failed", error={"message": str(exc)[:2000]})
            finally:
                self.running = None
                job["completed_at"] = time.time()
                for temp in [job.get("video_path"), job.get("image_path"), *job["temp"]]:
                    if temp:
                        Path(temp).unlink(missing_ok=True)


def build_app(engine: Engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        worker = asyncio.create_task(engine.worker())
        yield
        worker.cancel()

    app = FastAPI(lifespan=lifespan)
    args = engine.args

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": args.served_model_name, "object": "model",
                                            "owned_by": "Lightricks"}]}

    @app.post("/v1/videos")
    async def create(request: Request):
        uploads = []
        if request.headers.get("content-type", "").startswith("multipart/"):
            form = await request.form()
            fields = {k: v for k, v in form.items() if isinstance(v, str)}
            uploads = [u for key in ("input_reference", "input_references")
                       for u in form.getlist(key) if hasattr(u, "read")]
        else:
            fields = await request.json()
        extra = fields.get("extra_params") or {}
        if isinstance(extra, str):
            extra = json.loads(extra)
        prompt = str(fields.get("prompt") or "").strip()
        if not prompt:
            raise HTTPException(400, "prompt is required")
        width, height = (int(x) for x in str(fields.get("size") or args.default_size).lower().split("x"))
        if width % 64 or height % 64:
            raise HTTPException(400, f"size {width}x{height}: both sides must be multiples of 64 "
                                     "(the two-stage recipe renders half of it first)")
        fps = int(fields.get("fps") or args.fps)
        frames = fields.get("num_frames")
        num_frames = int(frames) if frames else frames_for(float(fields.get("seconds") or args.seconds), fps)
        job_id = "video_" + secrets.token_hex(12)
        job = {"id": job_id, "object": "video", "status": "queued", "progress": 0,
               "created_at": time.time(), "prompt": prompt, "width": width, "height": height,
               "size": f"{width}x{height}", "fps": fps, "num_frames": num_frames,
               "seconds": round(num_frames / fps, 2),
               "seed": int(fields.get("seed") if fields.get("seed") not in (None, "") else secrets.randbelow(2**31)),
               "image_strength": float(extra.get("image_strength", 1.0)),
               "conditioning_strength": float(extra.get("conditioning_strength", 1.0)),
               "speed": float(extra.get("speed", 0.2)), "temp": []}
        if not 0.01 <= job["speed"] <= 1.0:
            raise HTTPException(400, "speed is the slow-motion factor, from 0.01 to 1.0 (real time)")
        for upload in uploads:
            name = (upload.filename or "").lower()
            kind = "video" if (upload.content_type or "").startswith("video/") or name.endswith(VIDEO_SUFFIXES) \
                else "image"
            key = "video_path" if kind == "video" else "image_path"
            if key in job:
                raise HTTPException(400, f"one {kind} at most")
            suffix = Path(name).suffix or (".mp4" if kind == "video" else ".png")
            fd, path = tempfile.mkstemp(prefix=job_id, suffix=suffix, dir=engine.out_dir)
            with os.fdopen(fd, "wb") as f:
                f.write(await upload.read())
            job[key] = path
        engine.jobs[job_id] = job
        await engine.queue.put(job_id)
        return public(job)

    @app.get("/v1/videos/{job_id}")
    async def status(job_id: str):
        job = engine.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "no such video job")
        return public(job)

    @app.get("/v1/videos/{job_id}/content")
    async def content(job_id: str):
        job = engine.jobs.get(job_id)
        if not job or not job.get("path"):
            raise HTTPException(404, "no such finished video")
        return FileResponse(job["path"], media_type="video/mp4")

    @app.get("/v1/list_loras")
    async def list_loras():
        on = [{"nickname": engine.lora["nickname"], "strength": engine.lora["strength"]}] if engine.lora else []
        return {"loaded_adapters": on, "active": {"transformer": on} if on else {}}

    @app.post("/v1/set_lora")
    async def set_lora(request: Request):
        body = await request.json()
        path = body.get("lora_path")
        if not path or not Path(path).is_file():
            raise HTTPException(400, f"no LoRA file at {path!r}")
        if any(j["status"] in ("queued", "in_progress") for j in engine.jobs.values()):
            raise HTTPException(409, "a render is queued or running; switch after it")
        pipeline = body.get("pipeline")
        if pipeline not in (None, "ic", "ic-one-stage", "alpha-gen", "hdr", "slow-motion"):
            raise HTTPException(400, f"unknown pipeline {pipeline!r}")
        embeddings = body.get("text_embeddings_path")
        if pipeline == "hdr" and not (embeddings and Path(embeddings).is_file()):
            raise HTTPException(400, "the HDR pipeline needs the LoRA's text_embeddings_path")
        ic = pipeline in ("ic", "ic-one-stage")
        engine.lora = {"nickname": body.get("lora_nickname") or Path(path).stem, "path": path,
                       "strength": float(body.get("strength") or 1.0), "ic": ic or is_ic_lora(path),
                       "one_stage": pipeline == "ic-one-stage", "pipeline": None if ic else pipeline,
                       "text_embeddings_path": embeddings}
        return {"message": f"{engine.lora['nickname']} is on", "ic_lora": engine.lora["ic"],
                "pipeline": pipeline or ("ic" if engine.lora["ic"] else "distilled")}

    @app.post("/v1/unmerge_lora_weights")
    async def unmerge():
        if any(j["status"] in ("queued", "in_progress") for j in engine.jobs.values()):
            raise HTTPException(409, "a render is queued or running; switch after it")
        engine.lora = None
        return {"message": "no LoRA"}

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
    p.add_argument("--transformer-path", required=True, help="the distilled DiT")
    p.add_argument("--full-transformer-path", default=None,
                   help="the full (dev) DiT, for pipelines that need it (alpha-gen)")
    p.add_argument("--text-encoder-path", required=True)
    p.add_argument("--video-vae-path", required=True)
    p.add_argument("--audio-vae-path", required=True)
    p.add_argument("--spatial-upsampler-path", required=True)
    p.add_argument("--duration-head-path", default=None)
    p.add_argument("--served-model-name", default="Lightricks/LTX-2.5")
    p.add_argument("--default-size", default="1344x768")
    p.add_argument("--fps", type=int, default=24)
    p.add_argument("--seconds", type=float, default=5)
    p.add_argument("--output-path", default="/tmp/ltx-outputs")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=30000)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    engine = Engine(args)
    logging.getLogger().addHandler(ProgressFromLog(engine))
    uvicorn.run(build_app(engine), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
