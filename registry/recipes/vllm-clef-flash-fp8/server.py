"""Clef-Flash on the Jev / SystemOne API: POST /v1/systemone, with vLLM running the backbone.

Clef is Qwen3.5-9B (with its vision encoder) plus Cloudflare's joint schema head,
which reads the final hidden state of every prompt token and scores every
allowed answer. vLLM runs the backbone as a pooling model that returns exactly
those hidden states (task token_embed, ALL pooling); the head is Cloudflare's own
JointSchemaHead from joint_schema_model.py, run here on the same GPU.

Prompts are built by Cloudflare's own encode_record, so every text token is the
one Clef was trained on. Each image or video goes in as its single placeholder,
and vLLM expands it into vision tokens; the spans of the questions and options
move by that expansion. Requests are batched by vLLM, so several decisions run
at once.

A request is Jev's body (model, state, questions) plus optional `images` and
`videos`, each a list of base64 files. Images are decoded with Pillow; a video
is sampled to frames here, at --video-fps, and handed to vLLM with the times
they were taken at, which the model reads as timestamps.
"""
from __future__ import annotations

import asyncio
import base64
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
from safetensors.torch import load_file
from transformers import AutoTokenizer
from vllm import PoolingParams
from vllm.engine.arg_utils import AsyncEngineArgs
from vllm.utils.argparse_utils import FlexibleArgumentParser
from vllm.v1.engine.async_llm import AsyncLLM


class _Placeholders:
    """Stands in for the processor in encode_record: the media become their bare
    placeholders, which vLLM expands into vision tokens itself."""

    def __call__(self, text, **_):
        return {"input_ids": torch.tensor([tokenizer(text[0], add_special_tokens=False).input_ids])}


PLACEHOLDERS = _Placeholders()
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


def _check(body: dict) -> dict:
    questions = body.get("questions")
    if "state" not in body:
        raise HTTPException(400, "state is required")
    if not isinstance(questions, dict) or not questions:
        raise HTTPException(400, "at least one question is required")
    for question_id, question in questions.items():
        if not isinstance(question, dict) or question.get("type") not in QUESTION_TYPES:
            raise HTTPException(400, f"{question_id}: type must be noul, choice, or score")
        if question["type"] != "noul" and not question.get("criteria"):
            raise HTTPException(400, f"{question_id}: criteria must not be empty")
    return questions


def _shifted(enc: EncodedRecord, by: int) -> EncodedRecord:
    """The record with its question and option spans moved past the vision tokens."""
    if not by:
        return enc
    move = lambda span: (span[0] + by, span[1] + by)  # noqa: E731
    return EncodedRecord(
        input_ids=enc.input_ids, record_id=enc.record_id, media=None,
        questions=tuple(EncodedQuestion(question_id=q.question_id, question_type=q.question_type,
                                        question_span=move(q.question_span),
                                        option_spans=tuple(move(s) for s in q.option_spans),
                                        option_ids=q.option_ids) for q in enc.questions))


@torch.inference_mode()
def _answer(enc: EncodedRecord, ids: list[int], hidden: torch.Tensor) -> list[torch.Tensor]:
    # The head reads the output embedding only at the option tokens: those few
    # rows come from the weights file, and the ids are renumbered to match them.
    spans = [range(*span) for q in enc.questions for span in q.option_spans]
    words = sorted({ids[i] for span in spans for i in span})
    rows = torch.cat([output_embedding[w:w + 1] for w in words]).to("cuda", torch.bfloat16)
    renumbered = [0] * len(ids)
    for span in spans:
        for i in span:
            renumbered[i] = words.index(ids[i])
    input_ids = torch.tensor([renumbered], device="cuda")
    mask = torch.ones_like(input_ids)
    return head(hidden.to("cuda", torch.bfloat16).unsqueeze(0), input_ids, mask, [enc], rows)[0]


@app.get("/health")
async def health():
    await engine.check_health()
    return {"status": "ok"}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": served_name, "object": "model", "owned_by": "cloudflare"}]}


@app.post("/v1/systemone")
async def decide(body: dict):
    questions = _check(body)
    images = body.pop("images", None) or []
    videos = body.pop("videos", None) or []
    if not isinstance(images, list) or not isinstance(videos, list):
        raise HTTPException(400, "images and videos must be lists")
    if len(images) > args.max_images or len(videos) > args.max_videos:
        raise HTTPException(400, f"at most {args.max_images} images and "
                                 f"{args.max_videos} videos per request")
    media: dict = {}
    if images:
        media["image"] = await asyncio.to_thread(lambda: [_image(_decode(i, "image")) for i in images])
    if videos:
        media["video"] = await asyncio.to_thread(lambda: [_video(_decode(v, "video")) for v in videos])
    record = {**body, "images": media.get("image"), "videos": media.get("video")}
    try:
        # One token short of --max-model-len: vLLM 0.31 never schedules a pooling
        # prompt of exactly that length, and the request waits forever.
        enc = encode_record(tokenizer, record, max_length=args.max_model_len - 1,
                            processor=PLACEHOLDERS if media else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    prompt = {"prompt_token_ids": list(enc.input_ids)}
    if media:
        prompt["multi_modal_data"] = media
    out = None
    try:
        async for out in engine.encode(prompt, PoolingParams(task="token_embed"), str(uuid.uuid4())):
            pass
    except ValueError as exc:      # e.g. the prompt, with its vision tokens, is too long
        raise HTTPException(400, str(exc)) from None
    ids = list(out.prompt_token_ids)
    enc = _shifted(enc, len(ids) - len(enc.input_ids))
    logits = await asyncio.to_thread(_answer, enc, ids, out.outputs.data)
    answers = {
        q.question_id: systemone_answer(questions[q.question_id],
                                        dict(zip(q.option_ids, l.float().softmax(-1).tolist())))
        for q, l in zip(enc.questions, logits)
    }
    return {"model": served_name, "answers": answers,
            "usage": {"input_tokens": len(ids), "output_tokens": 0}}


@app.exception_handler(HTTPException)
def _error(_, exc: HTTPException):
    return JSONResponse({"error": {"message": exc.detail}}, status_code=exc.status_code)


# Everything that touches the GPU runs only here: vLLM starts its engine in a
# spawned process that imports this file again, and must find nothing to run.
if __name__ == "__main__":
    parser = FlexibleArgumentParser()
    parser.add_argument("--max-images", type=int, required=True)
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
    from joint_schema_model import (  # noqa: E402
        QUESTION_TYPES, EncodedQuestion, EncodedRecord, JointSchemaHead, encode_record,
        systemone_answer,
    )

    tokenizer = AutoTokenizer.from_pretrained(args.model)

    # The head, and the output embedding it reads option words from (the backbone's
    # lm_head, which a pooling model does not load). The embedding stays in the
    # weights file, memory-mapped: a request reads only its option words' rows,
    # instead of 2 GB held in memory.
    head = JointSchemaHead(**json.loads(open(f"{args.model}/joint_head_config.json").read()))
    head.load_state_dict(load_file(f"{args.model}/joint_head.safetensors"), strict=True)
    head = head.to("cuda", torch.bfloat16).eval()
    _index = json.loads(open(f"{args.model}/model.safetensors.index.json").read())["weight_map"]
    _key = next(k for k in _index if k.endswith("lm_head.weight"))
    weights_file = safe_open(f"{args.model}/{_index[_key]}", "pt", device="cpu")
    output_embedding = weights_file.get_slice(_key)

    engine = AsyncLLM.from_engine_args(engine_args)

    uvicorn.run(app, host=args.host, port=args.port)
