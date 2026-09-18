"""MCP server: image generation and editing on the Spark, for any agent.

Speaks MCP's Streamable HTTP transport in its stateless form, at POST /mcp:
one JSON-RPC request per POST, no session id, no server-initiated stream (GET
answers 405, which the spec defines as "this server offers none"). That is the
whole of the transport an MCP client needs to list and call tools, and it keeps
the daemon free of an SDK and of per-client session state.

Every request is plain JSON except tools/call, which answers as an SSE stream
when the client accepts one (every Streamable HTTP client must). A render takes
minutes, and a silent socket that long trips client read timeouts — Hermes, for
one, gives up after 300s of no bytes. The stream carries a keepalive comment
every few seconds, and progress notifications when the client asked for them.

Authentication is the Hub's own: /mcp is guarded by AuthMiddleware like /v1, so
a client sends the same API key it uses for the LLM.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from daemon.services import audio_service, image_service, video_service
from daemon.services.connect_service import request_origin

router = APIRouter(tags=["mcp"])

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "spark-ai-hub-images", "title": "Spark AI Hub Images", "version": "1.0.0"}
KEEPALIVE_SECONDS = 10

INSTRUCTIONS = (
    "Generates and edits images, generates and edits videos, and composes music, with open models running on "
    "the user's DGX Spark. Call list_image_models / list_video_models to see which "
    "are running. Every result is saved on the Hub and returned as a URL plus a "
    "preview. Show the user the URL. To refine an image, pass its URL to edit_image.\n\n"
    "Renders are asynchronous jobs, because they take from seconds to many minutes. "
    "generate_image and edit_image return the image if it is ready within about 45 s; "
    "otherwise they return status \"rendering\" and a job_id. generate_video always "
    "returns a job_id. A job_id is not a failure and not the final result: the render "
    "is still running on the Spark. Collect it by calling get_image (images) or "
    "get_video (videos) with that job_id, and keep calling it until the status is "
    "completed or failed. Each call already waits up to wait_seconds, so no sleep is "
    "needed between calls. Keep polling in the same turn instead of telling the user "
    "to wait or check back, and never call generate_* again for the same request: that "
    "queues a second render behind the first. If a tool call times out, the job is "
    "not lost; call get_image / get_video again."
)

_ASYNC_IMAGE = (
    " Returns the image when it is ready within about 45 s; slower renders return "
    "status \"rendering\" and a job_id instead, which you must collect with get_image "
    "(call it until the image arrives; do not start the render again)."
)

_STEPS_HINT = (
    "Sampling steps. Leave this out: each model then uses the steps its model "
    "card recommends (list_image_models shows them — a step-distilled model "
    "wants 4, a full diffusion model 50). Set it only when the user asks."
)

_WAIT_HINT = (
    "How long this call waits for the job before reporting it still running. "
    "Omit for 120. Stay under ~240: some clients cut a tool call off after about 5 minutes."
)

_IMAGE_REFS = (
    "Each item is a URL returned by generate_image or edit_image, a public "
    "http(s) image URL, or a base64 data: URL. Local file paths do not work: "
    "the model runs on the Spark, not on this machine."
)

TOOLS = [
    {
        "name": "generate_image",
        "title": "Generate image",
        "description": (
            "Create an image from a text prompt on the Spark. Write a detailed, "
            "visual prompt (subject, setting, lighting, style, composition); put any "
            "text that must appear in the image in quotes." + _ASYNC_IMAGE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What the image should show."},
                "aspect_ratio": {"type": "string", "enum": image_service.ASPECT_RATIOS,
                                 "default": "1:1"},
                "negative_prompt": {"type": "string",
                                    "description": "Things to keep out of the image."},
                "seed": {"type": "integer", "minimum": 0,
                         "description": "Reuse a seed to reproduce an image. Omit for random."},
                "steps": {"type": "integer", "minimum": 1, "maximum": 60,
                          "description": _STEPS_HINT},
                "model": {"type": "string",
                          "description": "A model id from list_image_models. Omit to use whichever is running."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "edit_image",
        "title": "Edit image",
        "description": (
            "Edit one to three images with a natural-language instruction on the "
            "Spark, e.g. 'replace the background with a rainy neon street, keep the "
            "person unchanged'." + _ASYNC_IMAGE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "The edit to make."},
                "images": {"type": "array", "items": {"type": "string"},
                           "minItems": 1, "maxItems": 3, "description": _IMAGE_REFS},
                "seed": {"type": "integer", "minimum": 0},
                "steps": {"type": "integer", "minimum": 1, "maximum": 60,
                          "description": _STEPS_HINT},
                "model": {"type": "string",
                          "description": "A model id from list_image_models. Omit to use whichever is running."},
            },
            "required": ["prompt", "images"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_image",
        "title": "Get image",
        "description": (
            "Collect an image job that generate_image or edit_image returned as still "
            "rendering. Waits up to wait_seconds; returns the image when done, or "
            "status \"rendering\" if not yet, in which case call get_image again with "
            "the same job_id, repeating until it completes or fails. The render "
            "continues on the Spark whatever this client does, so a timed-out call "
            "never loses the picture."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": image_service.MAX_WAIT,
                                 "default": 120, "description": _WAIT_HINT},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "generate_video",
        "title": "Generate video",
        "description": (
            "Start rendering a short video on the Spark from a text prompt, or from a "
            "starting image plus a prompt (image-to-video), or from an input video to "
            "edit, extend or inpaint (video-to-video). Asynchronous: returns a job_id at "
            "once, not the video. Rendering takes several minutes, so then call get_video "
            "with the job_id, again and again until it returns the video or an error; do "
            "not start the render again. Describe the motion and camera as well as the scene."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "What happens in the video."},
                "image": {"type": "string",
                          "description": "Optional first frame for image-to-video. " + _IMAGE_REFS},
                "video": {"type": "string",
                          "description": ("Optional input video to edit (models that list "
                                          "video-to-video). A URL returned by get_video, a public "
                                          "http(s) video URL, or a base64 data: URL.")},
                "seconds": {"type": "integer", "minimum": 1, "maximum": 10,
                            "description": "Length. Omit for the model's default."},
                "aspect_ratio": {"type": "string", "enum": video_service.ASPECT_RATIOS,
                                 "default": "16:9"},
                "seed": {"type": "integer", "minimum": 0},
                "steps": {"type": "integer", "minimum": 1, "maximum": 100,
                          "description": "Leave this out: the model card's setting is used."},
                "model": {"type": "string",
                          "description": "A model id from list_video_models. Omit to use whichever is running."},
            },
            "required": ["prompt"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_video",
        "title": "Get video",
        "description": (
            "Collect a video job started by generate_video. Waits up to wait_seconds for it "
            "to finish; returns the video URL and a still frame when done, or its "
            "progress if still rendering, in which case call get_video again with the same "
            "job_id, repeating until it completes or fails."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "wait_seconds": {"type": "integer", "minimum": 0, "maximum": video_service.MAX_WAIT,
                                 "default": 120, "description": _WAIT_HINT},
            },
            "required": ["job_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_video_models",
        "title": "List video models",
        "description": ("List the Spark's video models: what each accepts, whether it is "
                        "running, and the settings its model card recommends."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "generate_music",
        "title": "Generate music",
        "description": (
            "Compose a song on the Spark: lyrics plus a style caption in, a stereo WAV out. "
            "Put structure tags ([Verse], [Chorus], [Bridge], [Outro]) each on its own line "
            "in the lyrics. Describe genre, instruments, tempo (BPM), mood and production "
            "in style. Can take several minutes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lyrics": {"type": "string", "description": "The lyrics, with structure tags on their own lines."},
                "style": {"type": "string", "description": "Genre, instrumentation, tempo, mood, production."},
                "seconds": {"type": "integer", "minimum": 5, "maximum": audio_service.MAX_SECONDS,
                            "description": "Maximum length; the model may end the song sooner. Omit for 30."},
                "seed": {"type": "integer", "minimum": 0},
                "model": {"type": "string", "description": "Omit to use whichever music model is running."},
            },
            "required": ["lyrics", "style"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_image_models",
        "title": "List image models",
        "description": ("List the Spark's image models: what each does, whether it is "
                        "running, and the settings its model card recommends."),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
]
_TOOL_NAMES = {t["name"] for t in TOOLS}


def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _text(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _sse(message: dict) -> str:
    return f"event: message\ndata: {json.dumps(message)}\n\n"


# ------------------------------------------------------------------- tools


def _int(args: dict, key: str) -> int | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise image_service.ImageError(f"'{key}' must be an integer.") from None


async def call_tool(name: str, args: dict, origin: str, on_progress=None) -> dict:
    """Run one tool; failures come back as an isError result, never raise."""
    try:
        if name == "list_image_models":
            return _text(json.dumps(await image_service.list_models(), indent=2))
        if name == "generate_music":
            lyrics = str(args.get("lyrics") or "").strip()
            style = str(args.get("style") or "").strip()
            if not lyrics or not style:
                raise image_service.ImageError("'lyrics' and 'style' are both required.")
            info = await audio_service.generate(
                lyrics=lyrics, style=style, seconds=_int(args, "seconds"), seed=_int(args, "seed"),
                model=str(args.get("model") or "").strip() or None, on_progress=on_progress,
            )
            url = f"{origin}{audio_service.PUBLIC_PREFIX}/{info['audio_id']}.wav"
            lines = [f"Music: {url}", f"Model: {info['model']}",
                     f"Length: {info['seconds']}s", f"Seed: {info['seed']}"]
            return {"content": [{"type": "text", "text": "\n".join(lines)}],
                    "structuredContent": {**info, "url": url}, "isError": False}
        if name == "get_image":
            job_id = str(args.get("job_id") or "").strip()
            if not job_id:
                raise image_service.ImageError("'job_id' is required.")
            wait = _int(args, "wait_seconds")
            info = await image_service.check_job(job_id, 120 if wait is None else wait)
            return _image_result(info, origin)
        if name == "list_video_models":
            return _text(json.dumps(await video_service.list_models(), indent=2))
        if name == "get_video":
            job_id = str(args.get("job_id") or "").strip()
            if not job_id:
                raise image_service.ImageError("'job_id' is required.")
            wait = _int(args, "wait_seconds")
            info = await video_service.check(job_id, 120 if wait is None else wait, on_progress)
            return _video_result(info, origin)
        if name == "generate_video":
            prompt = str(args.get("prompt") or "").strip()
            if not prompt:
                raise image_service.ImageError("'prompt' is required.")
            info = await video_service.start(
                prompt=prompt, image=str(args.get("image") or "").strip() or None,
                video=str(args.get("video") or "").strip() or None,
                seconds=_int(args, "seconds"), aspect_ratio=str(args.get("aspect_ratio") or "16:9"),
                seed=_int(args, "seed"), steps=_int(args, "steps"),
                model=str(args.get("model") or "").strip() or None,
            )
            lines = [f"Video job started: {info['job_id']}", f"Model: {info['model']}"]
            lines += [f"{k.capitalize()}: {info[k]}" for k in ("size", "seconds", "seed", "steps")
                      if info.get(k) is not None]
            lines.append("Not done yet: this is a job, not the video. Rendering takes several "
                         f"minutes. Now call get_video with job_id {info['job_id']}, and keep "
                         "calling it until it returns the video; each call waits up to "
                         "wait_seconds. Do not call generate_video again for this request.")
            return {"content": [{"type": "text", "text": "\n".join(lines)}],
                    "structuredContent": {**info, **_next_call("get_video", info["job_id"])},
                    "isError": False}

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            raise image_service.ImageError("'prompt' is required.")
        params = image_service.Params(
            prompt=prompt,
            negative_prompt=str(args.get("negative_prompt") or ""),
            aspect_ratio=str(args.get("aspect_ratio") or "1:1"),
            seed=_int(args, "seed"),
            steps=_int(args, "steps"),
        )
        model = str(args.get("model") or "").strip() or None

        if name == "generate_image":
            job_id = await image_service.start_job("generate", params, [], model)
        else:
            images = args.get("images")
            if isinstance(images, str):
                images = [images]
            if not isinstance(images, list) or not 1 <= len(images) <= 3:
                raise image_service.ImageError("'images' must list one to three images.")
            job_id = await image_service.start_job("edit", params, [str(i) for i in images], model)
        if on_progress:
            on_progress("rendering on the Spark")
        info = await image_service.check_job(job_id, image_service.JOB_GRACE)
        return _image_result(info, origin)
    except image_service.ImageError as exc:
        return _text(str(exc), is_error=True)
    except Exception as exc:  # noqa: BLE001 - surface anything else to the agent
        return _text(f"Image request failed: {type(exc).__name__}: {exc}", is_error=True)


def _next_call(tool: str, job_id: str) -> dict:
    """What an unfinished job's result tells a client that reads structuredContent only."""
    return {"done": False, "next_call": {"tool": tool, "arguments": {"job_id": job_id}}}


def _image_result(info: dict, origin: str) -> dict:
    """One shape for both paths: the finished image, or the job to collect."""
    if info["status"] == "failed":
        return _text(info.get("error") or f"Image job {info['job_id']} failed.", is_error=True)
    if info["status"] != "completed":
        return {"content": [{"type": "text", "text": (
                    f"Not done yet: still rendering on the Spark ({info['elapsed']}s so far). "
                    f"This is a job, not the image. Now call get_image with job_id "
                    f"{info['job_id']}, and keep calling it until it returns the image; the "
                    f"render finishes even if a call times out. Do not start the render again.")}],
                "structuredContent": {**{k: v for k, v in info.items() if k != "result"},
                                      **_next_call("get_image", info["job_id"])},
                "isError": False}
    result = info["result"]
    url = f"{origin}{result.path}"
    lines = [
        f"Image: {url}",
        f"Model: {result.model}",
        f"Size: {result.width}x{result.height}",
    ]
    if result.seed is not None:
        lines.append(f"Seed: {result.seed}")
    if result.steps is not None:
        lines.append(f"Steps: {result.steps}")
    if result.preset:
        lines.append(f"Preset: {result.preset}")
    lines.append("Pass this URL to edit_image to change it further.")
    return {
        "content": [
            {"type": "text", "text": "\n".join(lines)},
            {"type": "image", "data": result.preview_jpeg_b64, "mimeType": "image/jpeg"},
        ],
        "structuredContent": {
            "url": url, "model": result.model, "width": result.width,
            "height": result.height, "seed": result.seed, "steps": result.steps,
        },
        "isError": False,
    }


def _video_result(info: dict, origin: str) -> dict:
    details = [f"{k.capitalize()}: {info[k]}" for k in ("size", "seconds", "seed", "steps")
               if info.get(k) is not None]
    if info["status"] == "failed":
        return _text(f"Video job {info['job_id']} failed on {info['model']}: {info.get('error')}",
                     is_error=True)
    if info["status"] != "completed":
        return {"content": [{"type": "text", "text": (
                    f"Not done yet: still rendering on {info['model']} ({info['status']}, "
                    f"{info.get('progress', 0)}% done). Call get_video again with job_id "
                    f"{info['job_id']}, and keep calling it until it returns the video.")}],
                "structuredContent": {**{k: v for k, v in info.items() if k != "poster"},
                                      **_next_call("get_video", info["job_id"])},
                "isError": False}
    url = f"{origin}{video_service.PUBLIC_PREFIX}/{info['video_id']}.mp4"
    lines = [f"Video: {url}", f"Model: {info['model']}", *details]
    if info.get("inference_time_s"):
        lines.append(f"Render time: {round(info['inference_time_s'])}s")
    content = [{"type": "text", "text": "\n".join(lines)}]
    if info.get("poster"):
        content.append({"type": "image", "data": info["poster"], "mimeType": "image/jpeg"})
    structured = {k: v for k, v in info.items() if k != "poster"}
    return {"content": content, "structuredContent": {**structured, "url": url}, "isError": False}


def _origin(request: Request) -> str:
    return request_origin(request) or f"{request.url.scheme}://{request.url.netloc}"


async def _tools_call(request: Request, msg_id: Any, params: dict) -> Response:
    name = params.get("name")
    if name not in _TOOL_NAMES:
        return JSONResponse(_error(msg_id, -32602, f"Unknown tool: {name}"))
    args = params.get("arguments") or {}
    if not isinstance(args, dict):
        return JSONResponse(_error(msg_id, -32602, "arguments must be an object"))
    origin = _origin(request)
    token = (params.get("_meta") or {}).get("progressToken")

    if "text/event-stream" not in request.headers.get("accept", ""):
        return JSONResponse(_result(msg_id, await call_tool(name, args, origin)))

    progress: asyncio.Queue[str] = asyncio.Queue()
    task = asyncio.create_task(call_tool(name, args, origin, progress.put_nowait))

    async def stream():
        count = 0
        try:
            while not task.done():
                getter = asyncio.ensure_future(progress.get())
                done, _ = await asyncio.wait({task, getter}, timeout=KEEPALIVE_SECONDS,
                                             return_when=asyncio.FIRST_COMPLETED)
                if getter in done:
                    if token is not None:
                        count += 1
                        yield _sse({"jsonrpc": "2.0", "method": "notifications/progress",
                                    "params": {"progressToken": token, "progress": count,
                                               "message": getter.result()}})
                else:
                    getter.cancel()
                    if not done:
                        yield ": keepalive\n\n"
            yield _sse(_result(msg_id, task.result()))
        finally:
            # The client hung up: stop waiting. The render already queued in the
            # app finishes on its own; there is no way to recall it.
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ routes


@router.post("/mcp")
async def mcp_post(request: Request):
    try:
        msg = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        return JSONResponse(_error(None, -32600, "Expected one JSON-RPC 2.0 message"),
                            status_code=400)

    # Notifications and responses expect no reply beyond the acknowledgement.
    if "id" not in msg or "method" not in msg:
        return Response(status_code=202)

    msg_id, method = msg["id"], msg["method"]
    params = msg.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        return JSONResponse(_result(msg_id, {
            "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": INSTRUCTIONS,
        }))
    if method == "ping":
        return JSONResponse(_result(msg_id, {}))
    if method == "tools/list":
        return JSONResponse(_result(msg_id, {"tools": TOOLS}))
    if method == "tools/call":
        return await _tools_call(request, msg_id, params)
    return JSONResponse(_error(msg_id, -32601, f"Method not found: {method}"))


@router.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_no_stream():
    return Response(status_code=405, headers={"Allow": "POST"})


@router.get("/audio/{name}")
async def audio_file(name: str):
    """A generated song. Public on purpose: the unguessable id is the access."""
    if not audio_service.AUDIO_NAME_RE.match(name):
        return Response(status_code=404)
    path = audio_service.AUDIO_DIR / name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="audio/wav",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/videos/{name}")
async def video_file(name: str):
    """A generated video. Public on purpose: the unguessable id is the access."""
    if not video_service.VIDEO_NAME_RE.match(name):
        return Response(status_code=404)
    path = video_service.VIDEO_DIR / name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="video/mp4",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/images/{name}")
async def image_file(name: str):
    """A generated image. Public on purpose: the unguessable id is the access."""
    if not image_service.IMAGE_NAME_RE.match(name):
        return Response(status_code=404)
    path = image_service.IMAGE_DIR / name
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(path, media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
