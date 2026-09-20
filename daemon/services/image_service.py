"""Image generation and editing behind the Hub's MCP server.

Every image model is an **OpenAI-images server** (SGLang Diffusion): it speaks
the standard /v1/images/generations and /v1/images/edits API, so one client
here serves every model. A recipe opts in with the `openai-images` tag, its
`text-to-image` / `image-edit` tags say which tools it answers, and its
`image_defaults` carry the settings its model card recommends. Adding a model
is a recipe, never code.

Servers are reached through the front door's probe route, so no recipe
publishes a port for this. Results are copied into the Hub's own data dir and
served at /images/<id>.png, to the account that made it only (media_store):
the link opens with that user's Hub sign-in or API key, and handed back to
`edit_image` it is read straight off disk. The id is 128 random bits besides,
so nobody can enumerate the rest.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import io
import contextlib
import json
import math
import time
import re
import secrets
import urllib.parse
from dataclasses import dataclass, replace
from typing import Callable

import aiohttp
from PIL import Image, ImageOps

from daemon.config import settings
from daemon.services import media_store, proxy_service, web_service
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services.registry_service import get_recipes

IMAGE_DIR = settings.data_dir / "images"
# Pictures users upload to edit (upload_service). Same URL shape as a result,
# so every tool takes them, but kept apart because they expire and results do not.
UPLOAD_DIR = IMAGE_DIR / "uploads"
PUBLIC_PREFIX = "/images"
IMAGE_NAME_RE = re.compile(r"^([0-9a-f]{32})\.png$")

OPENAI_IMAGES_TAG = "openai-images"
# Unified models that make images through a chat request (vLLM-Omni's
# SenseNova-U1): the prompt and any input images go in as a user message with
# "modalities": ["image"], and the picture comes back as a data URL.
OPENAI_CHAT_TAG = "openai-chat-images"
# HiDream-O1-Image served by HiDream's own app.py: a job is started with one
# JSON request (mode t2i / edit / subject) and the PNG arrives as the last
# server-sent event of /api/generate/stream/<job_id>.
HIDREAM_TAG = "hidream-app"
TAG_KINDS = {"text-to-image": "generate", "image-edit": "edit"}

PREVIEW_EDGE = 512            # longest side of the thumbnail returned inline
MAX_INPUT_BYTES = 20 * 1024 * 1024
JOB_TIMEOUT = 20 * 60         # a 50-step render of a 20B model fits well inside
MAX_SEED = 2**31 - 1


class ImageError(Exception):
    """A failure worth showing to the model verbatim."""


# ------------------------------------------------------------------ backends


@dataclass(frozen=True)
class Params:
    prompt: str
    negative_prompt: str = ""
    aspect_ratio: str = "1:1"
    seed: int | None = None
    steps: int | None = None


@dataclass(frozen=True)
class Backend:
    slug: str
    kinds: frozenset[str]         # {"generate"}, {"edit"} or both
    summary: str
    # The recipe's `image_defaults`: what the model card recommends.
    defaults: object | None = None
    # "images" (/v1/images/*) | "chat" (/v1/chat/completions) | "hidream" (app.py)
    protocol: str = "images"


# ~1 megapixel, sides multiples of 16, which every current DiT (FLUX.2,
# Z-Image, Qwen-Image) accepts.
OPENAI_SIZES = {
    "1:1": (1024, 1024), "16:9": (1344, 768), "9:16": (768, 1344),
    "4:3": (1152, 864), "3:4": (864, 1152), "3:2": (1216, 832), "2:3": (832, 1216),
}
ASPECT_RATIOS = list(OPENAI_SIZES)


def backends() -> dict[str, Backend]:
    """Every image model the catalog offers: recipes tagged `openai-images`
    (or `openai-chat-images` for unified chat models, `hidream-app` for
    HiDream's own server)."""
    found: dict[str, Backend] = {}
    for slug, recipe in sorted(get_recipes().items()):
        if OPENAI_IMAGES_TAG in recipe.tags:
            protocol = "images"
        elif OPENAI_CHAT_TAG in recipe.tags:
            protocol = "chat"
        elif HIDREAM_TAG in recipe.tags:
            protocol = "hidream"
        else:
            continue
        kinds = frozenset(TAG_KINDS[t] for t in recipe.tags if t in TAG_KINDS)
        if kinds:
            found[slug] = Backend(slug=slug, kinds=kinds, summary=recipe.name,
                                  defaults=getattr(recipe, "image_defaults", None),
                                  protocol=protocol)
    return found


def _tools(backend: Backend) -> list[str]:
    return [f"{k}_image" for k in ("generate", "edit") if k in backend.kinds]


def recommended(backend: Backend) -> dict | None:
    """The documented settings, as reported to agents; None if the recipe names none."""
    d = backend.defaults
    if d is None:
        return None
    out = {k: getattr(d, k) for k in ("preset", "steps", "guidance_scale", "true_cfg_scale")
           if getattr(d, k, None) is not None}
    if d.notes:
        out["notes"] = d.notes
    if d.source:
        out["source"] = d.source
    return out or None


def with_defaults(backend: Backend, params: Params) -> Params:
    """Fill the steps an agent left unset with the model card's recommendation."""
    if getattr(backend.defaults, "preset", None):
        # The preset owns the step count; the server rejects one set directly.
        return replace(params, steps=None)
    if params.steps is None and backend.defaults is not None and backend.defaults.steps:
        return replace(params, steps=backend.defaults.steps)
    return params


async def installed_backends() -> dict[str, Backend]:
    """The image models installed on the Spark; the catalog's others are not offered."""
    installed = await get_installed_slugs()
    return {slug: b for slug, b in backends().items() if slug in installed}


async def list_models() -> list[dict]:
    recipes = get_recipes()
    out = []
    for slug, backend in (await installed_backends()).items():
        recipe = recipes[slug]
        running = await is_recipe_running(slug)
        out.append({
            "model": slug,
            "name": recipe.name,
            "tools": _tools(backend),
            "summary": backend.summary,
            "state": ("ready" if running and is_ready(slug)
                      else "starting" if running else "stopped"),
            "min_memory_gb": recipe.requirements.min_memory_gb,
            **({"recommended": rec} if (rec := recommended(backend)) else {}),
        })
    return out


async def pick_backend(kind: str, model: str | None) -> Backend:
    """The backend to use: the one asked for, else one that is running."""
    installed = await installed_backends()
    candidates = [b for b in installed.values() if kind in b.kinds]
    if model:
        backend = installed.get(model)
        if backend is None or kind not in backend.kinds:
            names = ", ".join(b.slug for b in candidates) or "none installed"
            raise ImageError(f"'{model}' is not an installed model that can {kind} images. "
                             f"Installed models that can: {names}.")
        candidates = [backend]

    starting = []
    for backend in candidates:
        if await is_recipe_running(backend.slug):
            if is_ready(backend.slug):
                return backend
            starting.append(backend.slug)
    if starting:
        raise ImageError(f"{starting[0]} is still starting (loading weights). Call start_model "
                         f"with model {starting[0]} to wait until it is ready.")
    if not candidates:
        raise ImageError(f"No image model that can {kind} is installed on the Spark. "
                         f"Install one from the Spark AI Hub first.")
    names = " or ".join(b.slug for b in candidates)
    raise ImageError(f"No image model that can {kind} is running on the Spark. Start {names} "
                     f"with start_model first.")


# ---------------------------------------------------- OpenAI-images client


def edit_size(png: bytes) -> tuple[int, int]:
    """~1 megapixel at the input's aspect ratio, sides multiples of 16."""
    with Image.open(io.BytesIO(png)) as img:
        width, height = img.size
    scale = math.sqrt(1024 * 1024 / (width * height))
    return (max(256, round(width * scale / 16) * 16),
            max(256, round(height * scale / 16) * 16))


def _guidance(defaults) -> dict:
    if defaults is None:
        return {}
    return {k: getattr(defaults, k) for k in ("guidance_scale", "true_cfg_scale")
            if getattr(defaults, k) is not None}


def generation_body(params: Params, seed: int, defaults=None) -> dict:
    width, height = OPENAI_SIZES.get(params.aspect_ratio, OPENAI_SIZES["1:1"])
    body = {
        "prompt": params.prompt,
        "n": 1,
        "size": f"{width}x{height}",
        # `url` needs cloud storage configured on the server; b64 always works.
        "response_format": "b64_json",
        "output_format": "png",
        "seed": seed,
        **_guidance(defaults),
    }
    if preset := getattr(defaults, "preset", None):
        body["preset"] = preset
    elif params.steps:
        body["num_inference_steps"] = params.steps
    if params.negative_prompt:
        body["negative_prompt"] = params.negative_prompt
    return body


async def _openai_image(r: aiohttp.ClientResponse, slug: str) -> bytes:
    if r.status != 200:
        detail = (await r.text())[:500]
        raise ImageError(f"{slug} failed (HTTP {r.status}): {detail}")
    payload = await r.json()
    data = payload.get("data") or []
    if not data or not data[0].get("b64_json"):
        raise ImageError(f"{slug} returned no image.")
    return base64.b64decode(data[0]["b64_json"])


def chat_body(params: Params, seed: int, defaults, inputs: list[bytes]) -> dict:
    """A chat request asking a unified model for an image back."""
    if inputs:
        width, height = edit_size(inputs[0])
    else:
        width, height = OPENAI_SIZES.get(params.aspect_ratio, OPENAI_SIZES["1:1"])
    content = [{"type": "text", "text": params.prompt}]
    for png in inputs:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(png).decode()}})
    body = {
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image"],
        "height": height,
        "width": width,
        "seed": seed,
    }
    if params.steps:
        body["num_inference_steps"] = params.steps
    guidance = getattr(defaults, "guidance_scale", None)
    if guidance is not None:
        body["cfg_scale"] = guidance
    if params.negative_prompt:
        body["negative_prompt"] = params.negative_prompt
    return body


async def _render_chat(session: aiohttp.ClientSession, backend: Backend, params: Params,
                       inputs: list[bytes], seed: int) -> bytes:
    url = f"{proxy_service.internal_url(backend.slug)}/v1/chat/completions"
    async with session.post(url, headers=proxy_service.probe_headers(),
                            json=chat_body(params, seed, backend.defaults, inputs)) as r:
        if r.status != 200:
            raise ImageError(f"{backend.slug} failed (HTTP {r.status}): {(await r.text())[:500]}")
        payload = await r.json()
    for choice in payload.get("choices") or []:
        content = (choice.get("message") or {}).get("content")
        if isinstance(content, list):
            for item in content:
                data_url = ((item or {}).get("image_url") or {}).get("url", "")
                if data_url.startswith("data:") and "," in data_url:
                    return base64.b64decode(data_url.split(",", 1)[1])
    raise ImageError(f"{backend.slug} returned no image.")


def hidream_body(params: Params, seed: int, inputs: list[bytes]) -> dict:
    """A job request for HiDream's app.py. One reference image is an edit, two
    or more are subject personalization; the schedule is fixed by the server."""
    width, height = OPENAI_SIZES.get(params.aspect_ratio, OPENAI_SIZES["1:1"])
    body = {
        "prompt": params.prompt,
        "mode": "t2i" if not inputs else "edit" if len(inputs) == 1 else "subject",
        # HiDream is pixel-native at 2048x2048: twice the ~1 MP sizes.
        "width": width * 2,
        "height": height * 2,
        "seed": seed,
    }
    if inputs:
        body["refs_b64"] = [base64.b64encode(png).decode() for png in inputs]
        # An edit keeps the input's shape, like the other backends' edits.
        body["keep_original_aspect"] = len(inputs) == 1
    return body


def parse_sse(buffer: bytes) -> tuple[list[dict], bytes]:
    """Complete server-sent events in `buffer`, and the incomplete rest.

    Split on blank lines rather than read line by line: HiDream sends the
    finished image as one base64 line of several megabytes."""
    events = []
    *complete, rest = buffer.replace(b"\r\n", b"\n").split(b"\n\n")
    for block in complete:
        data = b"".join(line[5:].strip() for line in block.split(b"\n")
                        if line.startswith(b"data:"))
        if data:
            events.append(json.loads(data))
    return events, rest


async def _render_hidream(session: aiohttp.ClientSession, backend: Backend, params: Params,
                          inputs: list[bytes], seed: int) -> bytes:
    base = proxy_service.internal_url(backend.slug)
    async with session.post(f"{base}/api/generate/start", headers=proxy_service.probe_headers(),
                            json=hidream_body(params, seed, inputs)) as r:
        if r.status != 200:
            raise ImageError(f"{backend.slug} failed (HTTP {r.status}): {(await r.text())[:500]}")
        job_id = (await r.json()).get("job_id")
    if not job_id:
        raise ImageError(f"{backend.slug} did not start a job.")
    async with session.get(f"{base}/api/generate/stream/{job_id}", headers=proxy_service.probe_headers()) as r:
        if r.status != 200:
            raise ImageError(f"{backend.slug} failed (HTTP {r.status}): {(await r.text())[:500]}")
        buffer = b""
        async for chunk in r.content.iter_any():
            events, buffer = parse_sse(buffer + chunk)
            for event in events:
                if event.get("type") == "done" and event.get("image"):
                    return base64.b64decode(event["image"])
                if event.get("type") == "error":
                    raise ImageError(f"{backend.slug} failed: {event.get('message', 'unknown error')}")
    raise ImageError(f"{backend.slug} returned no image.")


async def _render(session: aiohttp.ClientSession, backend: Backend, kind: str,
                  params: Params, inputs: list[bytes], seed: int) -> bytes:
    if backend.protocol == "chat":
        return await _render_chat(session, backend, params, inputs, seed)
    if backend.protocol == "hidream":
        return await _render_hidream(session, backend, params, inputs, seed)
    base = f"{proxy_service.internal_url(backend.slug)}/v1/images"
    if kind == "generate":
        async with session.post(f"{base}/generations", headers=proxy_service.probe_headers(),
                                json=generation_body(params, seed, backend.defaults)) as r:
            return await _openai_image(r, backend.slug)

    width, height = edit_size(inputs[0])
    form = aiohttp.FormData()
    for i, png in enumerate(inputs):
        form.add_field("image", png, filename=f"input{i}.png", content_type="image/png")
    fields = {
        "prompt": params.prompt, "n": "1", "size": f"{width}x{height}",
        "response_format": "b64_json", "output_format": "png", "seed": str(seed),
        **{k: str(v) for k, v in _guidance(backend.defaults).items()},
    }
    if params.steps:
        fields["num_inference_steps"] = str(params.steps)
    for key, value in fields.items():
        form.add_field(key, value)
    async with session.post(f"{base}/edits", headers=proxy_service.probe_headers(), data=form) as r:
        return await _openai_image(r, backend.slug)


# ------------------------------------------------------------------ inputs


async def load_input(ref: str, session: aiohttp.ClientSession, user: dict | None = None,
                     *, suffix: str = ".png", max_bytes: int = MAX_INPUT_BYTES) -> bytes:
    """Raw bytes for an input image (or, with suffix='.mp4', video) given by URL.

    A Hub URL is read off disk and only for its owner: anyone else's file reads
    as missing, exactly like the media routes answer them. A public http(s) URL
    is fetched. Inline data (a data: URL) is refused on purpose: a model that
    types a file out as base64 burns its context and usually garbles the file,
    so the error points it at create_upload instead.
    """
    noun = "video" if suffix == ".mp4" else "image"
    ref = ref.strip()
    if (name := media_store.name_in_url(ref)) and name.endswith(suffix):
        if (path := media_store.find(name)) and await media_store.can_read(name, user):
            return path.read_bytes()
        raise ImageError(f"No Hub {noun} {name} for this account (it may have been deleted, "
                         "or it was an upload that expired — upload it again).")

    if ref.startswith("data:"):
        raise ImageError("Inline file data isn't accepted: tools take URLs. Call create_upload, "
                         "upload the file with the curl command it gives you, and pass its url.")

    parts = urllib.parse.urlsplit(ref)
    if parts.scheme in ("http", "https") and parts.hostname:
        port = parts.port or (443 if parts.scheme == "https" else 80)
        # Any signed-in user can make the daemon fetch this, so it must not be a
        # way to reach the daemon's own port or anything else on the LAN.
        if not await asyncio.to_thread(web_service.is_public, parts.hostname, port):
            raise ImageError(f"Only public http(s) {noun} URLs can be fetched.")
        async with session.get(ref, allow_redirects=False) as r:
            if r.status != 200:
                raise ImageError(f"Fetching {ref} failed (HTTP {r.status}).")
            body = await web_service.read_capped(r, max_bytes)
        if len(body) > max_bytes:
            raise ImageError(f"Input {noun} is larger than {max_bytes // 2**20} MB.")
        return body

    raise ImageError(f"{noun.capitalize()}s must be URLs: one returned by the Hub's tools, an "
                     "upload's url (create_upload), or a public http(s) URL. File paths do not "
                     "work: upload the file first.")


def to_png(raw: bytes) -> bytes:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            out = io.BytesIO()
            img.save(out, format="PNG")
            return out.getvalue()
    except Exception as exc:  # noqa: BLE001 - PIL raises many types
        raise ImageError(f"Not a readable image: {exc}") from None


# ----------------------------------------------------------------- results


@dataclass
class Result:
    image_id: str
    model: str
    width: int
    height: int
    seed: int | None
    preview_jpeg_b64: str
    steps: int | None = None
    preset: str | None = None

    @property
    def path(self) -> str:
        return f"{PUBLIC_PREFIX}/{self.image_id}.png"


def store(raw: bytes, model: str, seed: int | None) -> Result:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    image_id = secrets.token_hex(16)
    with Image.open(io.BytesIO(raw)) as img:
        img = img.convert("RGB")
        img.save(IMAGE_DIR / f"{image_id}.png", format="PNG")
        width, height = img.size
        img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    return Result(image_id, model, width, height, seed,
                  base64.b64encode(buf.getvalue()).decode())


# Renders in flight per model, image and music alike, so stop_model never
# pulls a model out from under one.
_rendering: collections.Counter[str] = collections.Counter()


@contextlib.contextmanager
def rendering(slug: str):
    _rendering[slug] += 1
    try:
        yield
    finally:
        _rendering[slug] -= 1


def renders_on(slug: str) -> int:
    return _rendering[slug]


async def run(kind: str, params: Params, images: list[str], model: str | None,
              on_progress: Callable[[str], None] | None = None,
              user: dict | None = None) -> Result:
    backend = await pick_backend(kind, model)
    params = with_defaults(backend, params)
    # Seeded here rather than by the server, so the caller can always be told
    # the seed that reproduces the image.
    seed = params.seed if params.seed is not None else secrets.randbelow(MAX_SEED)
    timeout = aiohttp.ClientTimeout(total=JOB_TIMEOUT, sock_connect=10)
    with rendering(backend.slug):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            inputs = [to_png(await load_input(ref, session, user)) for ref in images]
            if on_progress:
                on_progress(f"rendering on {backend.slug}")
            raw = await _render(session, backend, kind, params, inputs, seed)
    result = await asyncio.to_thread(store, raw, backend.slug, seed)
    await media_store.record(f"{result.image_id}.png", user and user["id"], backend.slug)
    result.steps = params.steps
    result.preset = getattr(backend.defaults, "preset", None)
    return result


# -------------------------------------------------------------------- jobs
# A render outlives the request that asked for it. MCP clients cancel a tool
# call on their own timeout (Claude's connector gives up in minutes), and until
# jobs existed that cancellation tore down the Hub's own request too: the model
# kept rendering, finished, and the picture was thrown away. Now the render runs
# in its own task and the result waits here to be collected.

JOB_GRACE = 45                # a fast model finishes inside the first call
MAX_WAIT = 600
MAX_JOBS = 200
_jobs: dict[str, dict] = {}


def _prune_jobs() -> None:
    while len(_jobs) >= MAX_JOBS:
        _jobs.pop(min(_jobs, key=lambda key: _jobs[key]["created"]))


async def start_job(kind: str, params: Params, images: list[str], model: str | None,
                    user: dict | None = None) -> str:
    """Start a render for `user` and return its job id at once."""
    _prune_jobs()
    job_id = secrets.token_hex(16)
    job: dict = {"job_id": job_id, "status": "rendering", "created": time.monotonic(),
                 "model": model, "kind": kind, "result": None, "error": None,
                 "user_id": user and user["id"], "done": asyncio.Event()}
    _jobs[job_id] = job

    async def render() -> None:
        try:
            job["result"] = await run(kind, params, images, model, user=user)
            job["status"] = "completed"
        except ImageError as exc:
            job["status"], job["error"] = "failed", str(exc)
        except Exception as exc:  # noqa: BLE001 - keep the reason for the agent
            job["status"], job["error"] = "failed", f"{type(exc).__name__}: {exc}"
        finally:
            job["elapsed"] = time.monotonic() - job["created"]
            job["done"].set()

    job["task"] = asyncio.create_task(render())
    return job_id


def owns_job(job: dict, user: dict | None) -> bool:
    """Only the account that started a job can collect it."""
    return not settings.auth_enabled or bool(user) and job.get("user_id") == user["id"]


async def check_job(job_id: str, wait: int, user: dict | None = None) -> dict:
    """The job's state, waiting up to `wait` seconds for it to finish."""
    job = _jobs.get(job_id)
    if job is None or not owns_job(job, user):
        raise ImageError(f"No image job {job_id} — it may have expired; start a new one.")
    if not job["done"].is_set() and wait > 0:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(job["done"].wait(), min(wait, MAX_WAIT))
    info = {k: job[k] for k in ("job_id", "status", "model", "kind", "result", "error")}
    info["elapsed"] = round(job.get("elapsed", time.monotonic() - job["created"]))
    return info
