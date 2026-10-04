"""Clef-Flash on the Jev / SystemOne API: POST /v1/systemone.

Cloudflare ships Clef as weights plus a reference loader (joint_schema_model.py,
in the model folder) and no server. This is that loader behind HTTP, nothing
more: one request at a time, answered by Cloudflare's own `systemone()`.

A request is Jev's body (model, state, questions) plus optional `images` and
`videos`, each a list of base64 files. Images are decoded with Pillow; a video
is sampled to frames here, at the processor's own rate (2 per second), because
the processor takes frames, not files.
"""
from __future__ import annotations

import argparse
import base64
import io
import sys
import threading

import av
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from transformers.video_utils import VideoMetadata

parser = argparse.ArgumentParser()
parser.add_argument("--model-path", required=True)
parser.add_argument("--served-model-name", required=True)
parser.add_argument("--max-length", type=int, required=True,
                    help="Token budget per request: state, media and questions together.")
parser.add_argument("--max-images", type=int, required=True)
parser.add_argument("--max-videos", type=int, required=True)
parser.add_argument("--max-image-pixels", type=int, required=True,
                    help="Larger pictures are scaled down to this many pixels first.")
parser.add_argument("--video-fps", type=float, required=True)
parser.add_argument("--max-video-frames", type=int, required=True)
parser.add_argument("--max-video-frame-pixels", type=int, required=True)
parser.add_argument("--host", required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()

sys.path.insert(0, args.model_path)
from joint_schema_model import load_release_model, systemone  # noqa: E402

model, processor = load_release_model(args.model_path, device="cuda")
lock = threading.Lock()
app = FastAPI()


def _decode(item: object, what: str) -> bytes:
    if not isinstance(item, str) or not item:
        raise HTTPException(400, f"each {what} must be a base64 string")
    if item.startswith("data:"):
        item = item.split(",", 1)[-1]
    try:
        return base64.b64decode(item, validate=True)
    except ValueError:
        raise HTTPException(400, f"an {what} is not valid base64") from None


def _shrink(img: Image.Image, max_pixels: int) -> Image.Image:
    if img.width * img.height > max_pixels:
        scale = (max_pixels / (img.width * img.height)) ** 0.5
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                         Image.Resampling.BICUBIC)
    return img


def _image(raw: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            return _shrink(ImageOps.exif_transpose(img).convert("RGB"), args.max_image_pixels)
    except (OSError, ValueError):
        raise HTTPException(400, "an image could not be decoded") from None


def _video(raw: bytes) -> tuple[np.ndarray, VideoMetadata]:
    """Frames at --video-fps, evenly thinned to --max-video-frames, as one uint8 array,
    with the times they were taken at, which the model reads as timestamps."""
    try:
        with av.open(io.BytesIO(raw)) as container:
            stream = container.streams.video[0]
            fps = float(stream.average_rate or args.video_fps)
            frames, times, next_t = [], [], 0.0
            for frame in container.decode(stream):
                t = float(frame.pts * stream.time_base) if frame.pts is not None else next_t
                if t + 1e-6 < next_t:
                    continue
                next_t = t + 1 / args.video_fps
                frames.append(_shrink(frame.to_image().convert("RGB"), args.max_video_frame_pixels))
                times.append(t)
    except (av.error.FFmpegError, IndexError, ValueError):
        raise HTTPException(400, "a video could not be decoded") from None
    if not frames:
        raise HTTPException(400, "a video has no frames")
    if len(frames) > args.max_video_frames:
        keep = np.linspace(0, len(frames) - 1, args.max_video_frames).round().astype(int)
        frames, times = [frames[i] for i in keep], [times[i] for i in keep]
    if len(frames) % 2:               # the vision tower takes frames in pairs
        frames.append(frames[-1])
        times.append(times[-1])
    size = frames[0].size
    indices = [round(t * fps) for t in times]
    metadata = VideoMetadata(total_num_frames=indices[-1] + 1, fps=fps, frames_indices=indices)
    return np.stack([np.asarray(f if f.size == size else f.resize(size)) for f in frames]), metadata


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": args.served_model_name, "object": "model",
                                        "owned_by": "cloudflare"}]}


@app.post("/v1/systemone")
def decide(body: dict):
    images = body.pop("images", None) or []
    videos = body.pop("videos", None) or []
    if not isinstance(images, list) or not isinstance(videos, list):
        raise HTTPException(400, "images and videos must be lists")
    if len(images) > args.max_images or len(videos) > args.max_videos:
        raise HTTPException(400, f"at most {args.max_images} images and "
                                 f"{args.max_videos} videos per request")
    record = {**body, "model": args.served_model_name}
    if images:
        record["images"] = [_image(_decode(i, "image")) for i in images]
    if videos:
        frames, metadata = zip(*(_video(_decode(v, "video")) for v in videos))
        record["videos"] = list(frames)
        # Frames are already sampled, so the processor must not sample them
        # again; their times become the timestamps in the prompt.
        record["media_kwargs"] = {"do_sample_frames": False, "video_metadata": list(metadata)}
    try:
        with lock:
            return systemone(model, processor, record, max_length=args.max_length)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    except torch.cuda.OutOfMemoryError:
        torch.cuda.empty_cache()
        raise HTTPException(413, "the request is too large for the free GPU memory") from None


@app.exception_handler(HTTPException)
def _error(_, exc: HTTPException):
    return JSONResponse({"error": {"message": exc.detail}}, status_code=exc.status_code)


uvicorn.run(app, host=args.host, port=args.port)
