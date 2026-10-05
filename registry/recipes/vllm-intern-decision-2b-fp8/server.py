"""Intern-Decision-2B on the Jev / SystemOne API: POST /v1/systemone, with vLLM running the model.

Intern-Decision is Qwen3.5-2B (with its vision encoder) fine-tuned to decide: the
prompt names every question's options with one-letter symbols and ends in a JSON
skeleton with a <decision> marker per question, and the answer to each question is
the model's next-token distribution over its symbols right before its marker. vLLM
runs the model as a pooling model that returns the final hidden state of every
prompt token; the scores of the symbols are those hidden states times the symbols'
rows of the output embedding, read from the weights file.

Prompts, symbols and calibration are InternLM's own (inference.py in the model
folder). Images go in as images, InternLM's trained input; a video goes in as
the model's own video input, sampled at --video-fps with the times the frames were
taken at, which the model reads as timestamps. Requests are batched by vLLM.

A request is Jev's body (model, state, questions) plus optional `images` and
`videos`, each a list of base64 files.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import sys
import uuid

import av
import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image, ImageOps
from safetensors import safe_open
from transformers import AutoTokenizer
from vllm import PoolingParams
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.utils.argparse_utils import FlexibleArgumentParser
from vllm.v1.engine.async_llm import AsyncLLM

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


def _video(raw: bytes) -> tuple[np.ndarray, dict]:
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
    metadata = {"fps": fps, "frames_indices": indices, "total_num_frames": indices[-1] + 1,
                "duration": (indices[-1] + 1) / fps, "video_backend": "pyav",
                "do_sample_frames": False}
    return np.stack([np.asarray(f if f.size == size else f.resize(size)) for f in frames]), metadata


def _media(images: list, videos: list) -> tuple[list[Image.Image], list[tuple[np.ndarray, dict]]]:
    return [_image(_decode(i, "image")) for i in images], [_video(_decode(v, "video")) for v in videos]


def _prompt(row: dict, n_images: int, n_videos: int) -> tuple[list[int], tuple[str, ...], dict]:
    """InternLM's prompt for the row, with one placeholder per picture and video."""
    compiled = compile_row(row)
    messages = copy.deepcopy(compiled.messages)
    if n_images or n_videos:
        messages[1]["content"] = ([{"type": "image"}] * n_images + [{"type": "video"}] * n_videos
                                  + [{"type": "text", "text": messages[1]["content"]}])
    text = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=False,
                                         enable_thinking=False, add_vision_id=True)
    return tokenizer(text, add_special_tokens=False).input_ids, compiled.fields, compiled.symbols


@torch.inference_mode()
def _answers(row: dict, fields, symbols, ids: list[int], hidden: torch.Tensor) -> dict:
    positions = [i - 1 for i, t in enumerate(ids) if t == marker_id]
    if len(positions) != len(fields):
        raise HTTPException(500, "decision markers do not match the questions")
    answers = {}
    for position, field in zip(positions, fields):
        question = row["questions"][field]
        values = [value for value, _ in _options(question)]
        token_ids = [symbol_ids[s] for s in symbols[field]]
        rows = torch.cat([embedding[t:t + 1] for t in token_ids]).float()
        probs = dict(zip(values, torch.softmax(rows @ hidden[position].float(), -1).tolist()))
        best = argmax(probs)
        answer = {"type": question["type"], "probabilities": probs, "confidence": probs[best]}
        if question["type"] == "noul":
            answer["noul"] = probs["yes"]
        elif question["type"] == "score":
            answer["score"] = sum(float(v) * probs[v] for v in values)
            answer["legend"] = dict(_options(question))
        else:
            answer["choice"] = best
        answers[field] = answer
    return scale_result({"answers": answers}, DEFAULT_TEMPERATURE)["answers"]


@app.get("/health")
async def health():
    await engine.check_health()
    return {"status": "ok"}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": served_name, "object": "model", "owned_by": "internlm"}]}


@app.post("/v1/systemone")
async def decide(body: dict):
    images = body.pop("images", None) or []
    videos = body.pop("videos", None) or []
    if not isinstance(images, list) or not isinstance(videos, list):
        raise HTTPException(400, "images and videos must be lists")
    if len(images) > MAX_IMAGES or len(videos) > args.max_videos:
        raise HTTPException(400, f"at most {MAX_IMAGES} images and {args.max_videos} videos per request")
    try:
        row = validate_request({"state": body.get("state"), "questions": body.get("questions")})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    pictures, clips = await asyncio.to_thread(_media, images, videos)
    ids, fields, symbols = _prompt(row, len(pictures), len(clips))
    prompt = {"prompt_token_ids": ids}
    if pictures or clips:
        prompt["multi_modal_data"] = {**({"image": pictures} if pictures else {}),
                                      **({"video": clips} if clips else {})}
    out = None
    try:
        async for out in engine.encode(prompt, PoolingParams(task="token_embed"), str(uuid.uuid4())):
            pass
    except ValueError as exc:      # e.g. the prompt, with its vision tokens, is too long
        raise HTTPException(400, str(exc)) from None
    ids = list(out.prompt_token_ids)
    answers = await asyncio.to_thread(_answers, row, fields, symbols, ids, out.outputs.data)
    return {"model": served_name, "answers": answers,
            "usage": {"input_tokens": len(ids), "output_tokens": 0}}


@app.exception_handler(HTTPException)
def _error(_, exc: HTTPException):
    return JSONResponse({"error": {"message": exc.detail}}, status_code=exc.status_code)


# Everything that touches the GPU runs only here: vLLM starts its engine in a
# spawned process that imports this file again, and must find nothing to run.
if __name__ == "__main__":
    parser = FlexibleArgumentParser()
    parser.add_argument("--max-videos", type=int, required=True)
    parser.add_argument("--max-image-pixels", type=int, required=True,
                        help="Larger pictures are scaled down to this many pixels first.")
    parser.add_argument("--video-fps", type=float, required=True)
    parser.add_argument("--max-video-frames", type=int, required=True)
    parser.add_argument("--max-video-frame-pixels", type=int, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser = AsyncEngineArgs.add_cli_args(parser)   # every vLLM engine flag, as on `vllm serve`
    args = parser.parse_args()
    engine_args = AsyncEngineArgs.from_cli_args(args)
    served_name = (args.served_model_name or [args.model])[0]

    sys.path.insert(0, args.model)
    from inference import (  # noqa: E402
        DECISION_TOKEN, DEFAULT_TEMPERATURE, _options, argmax, compile_row, scale_result,
        validate_request,
    )
    MAX_IMAGES = 8                    # InternLM's validate_request allows 1-8 images

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    marker_id = tokenizer.convert_tokens_to_ids(DECISION_TOKEN)
    symbol_ids = {}
    for s in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789":
        (symbol_ids[s],) = tokenizer.encode(s, add_special_tokens=False)

    # The output embedding (tied to the input embedding), memory-mapped from the
    # weights file: a request reads only its symbols' rows.
    _index = json.loads(open(f"{args.model}/model.safetensors.index.json").read())["weight_map"]
    _key = next(k for k in _index if k.endswith("embed_tokens.weight"))
    weights_file = safe_open(f"{args.model}/{_index[_key]}", "pt", device="cpu")
    embedding = weights_file.get_slice(_key)

    engine = AsyncLLM.from_engine_args(engine_args)

    uvicorn.run(app, host=args.host, port=args.port)
