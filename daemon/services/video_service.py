"""Video generation behind the Hub's MCP server.

Video models are **OpenAI-videos servers** (SGLang Diffusion): POST /v1/videos
queues a job and answers at once with its id, GET /v1/videos/{id} reports status
and progress, and GET /v1/videos/{id}/content serves the MP4 once it is done. A
render takes minutes — longer than an agent's tool call can wait — so the MCP
tools keep that shape: `generate_video` starts a job, `get_video` checks on it
(and can wait a while for it).

A recipe opts in with the `openai-videos` tag; `text-to-video`,
`image-to-video`, `video-edit`, `last-frame`, `multi-reference` and
`audio-reference` say what it accepts, and `video_defaults` carry its model
card's settings, including the shapes and lengths it renders. Finished videos are copied into the Hub's data dir and served at
/videos/<id>.mp4, private to the account that made them like images (media_store).

Jobs live in memory: a daemon restart forgets them, and so does the model
server, which keeps its own queue in memory too.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import aiohttp

from daemon.config import settings
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services import media_store, proxy_service
from daemon.services.image_service import ImageError, load_input, owns_job, to_png
from daemon.services.registry_service import get_recipes

VIDEO_DIR = settings.data_dir / "videos"
UPLOAD_DIR = VIDEO_DIR / "uploads"       # user uploads; they expire (upload_service)
PUBLIC_PREFIX = "/videos"
VIDEO_NAME_RE = re.compile(r"^([0-9a-f]{32})\.mp4$")

OPENAI_VIDEOS_TAG = "openai-videos"
TAG_KINDS = {"text-to-video": "text", "image-to-video": "image", "video-edit": "video",
             "last-frame": "last_image", "multi-reference": "images",
             "audio-reference": "audio"}
# What list_video_models calls each kind, so an agent can see every input a model takes.
ACCEPTS = {"text": "text-to-video", "image": "image-to-video", "video": "video-to-video",
           "last_image": "last-frame", "images": "multi-reference", "audio": "audio-reference"}
# Every shape any video model renders; each model's own list is in its video_defaults.
ASPECT_RATIOS = ["16:9", "9:16", "21:9", "4:3", "1:1", "3:4"]
DEFAULT_ASPECT_RATIOS = ["16:9", "9:16"]
DEFAULT_MAX_SECONDS = 10
MAX_SECONDS = 15
MAX_VIDEO_INPUT_BYTES = 200 * 1024 * 1024
MAX_AUDIO_INPUT_BYTES = 50 * 1024 * 1024
_KIND_LABELS = {"text": "make video from text", "image": "turn an image into video",
                "video": "edit a video", "last_image": "end on a given last frame",
                "images": "take several reference pictures",
                "audio": "take a reference sound"}

POLL_SECONDS = 5
MAX_WAIT = 600
MAX_JOBS = 200
MAX_SEED = 2**31 - 1


@dataclass(frozen=True)
class Backend:
    slug: str
    kinds: frozenset[str]         # values of TAG_KINDS
    summary: str
    defaults: object | None = None

    @property
    def aspect_ratios(self) -> list[str]:
        return getattr(self.defaults, "aspect_ratios", None) or DEFAULT_ASPECT_RATIOS

    @property
    def max_seconds(self) -> int:
        return getattr(self.defaults, "max_seconds", None) or DEFAULT_MAX_SECONDS


_jobs: dict[str, dict] = {}


def backends() -> dict[str, Backend]:
    """Every video model the catalog offers: recipes tagged `openai-videos`."""
    found: dict[str, Backend] = {}
    for slug, recipe in sorted(get_recipes().items()):
        if OPENAI_VIDEOS_TAG not in recipe.tags:
            continue
        kinds = frozenset(TAG_KINDS[t] for t in recipe.tags if t in TAG_KINDS)
        if kinds:
            found[slug] = Backend(slug=slug, kinds=kinds, summary=recipe.name,
                                  defaults=getattr(recipe, "video_defaults", None))
    return found


def recommended(backend: Backend) -> dict | None:
    d = backend.defaults
    if d is None:
        return None
    out = {k: getattr(d, k) for k in ("steps", "guidance_scale", "fps", "seconds", "size")
           if getattr(d, k, None) is not None}
    if d.notes:
        out["notes"] = d.notes
    if d.source:
        out["source"] = d.source
    return out or None


async def installed_backends() -> dict[str, Backend]:
    """The video models installed on the Spark; the catalog's others are not offered."""
    installed = await get_installed_slugs()
    return {slug: b for slug, b in backends().items() if slug in installed}


async def list_models() -> list[dict]:
    recipes = get_recipes()
    out = []
    for slug, backend in (await installed_backends()).items():
        running = await is_recipe_running(slug)
        out.append({
            "model": slug,
            "name": recipes[slug].name,
            "accepts": [ACCEPTS[k] for k in ACCEPTS if k in backend.kinds],
            "aspect_ratios": backend.aspect_ratios,
            "max_seconds": backend.max_seconds,
            "state": ("ready" if running and is_ready(slug)
                      else "starting" if running else "stopped"),
            "min_memory_gb": recipes[slug].requirements.min_memory_gb,
            **({"recommended": rec} if (rec := recommended(backend)) else {}),
        })
    return out


async def pick_backend(needs: set[str], model: str | None) -> Backend:
    label = " and ".join(_KIND_LABELS[k] for k in ACCEPTS if k in needs)
    installed = await installed_backends()
    candidates = [b for b in installed.values() if needs <= b.kinds]
    if model:
        backend = installed.get(model)
        if backend is None or not needs <= backend.kinds:
            names = ", ".join(b.slug for b in candidates) or "none installed"
            raise ImageError(f"'{model}' is not an installed model that can {label}. "
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
        raise ImageError(f"No video model that can {label} is installed on the Spark. "
                         f"Install one from the Spark AI Hub first.")
    names = " or ".join(b.slug for b in candidates)
    raise ImageError(f"No video model that can {label} is running on the Spark. "
                     f"Start {names} with start_model first.")


def video_fields(defaults, *, prompt: str, seconds: int | None, aspect_ratio: str,
                 seed: int, steps: int | None) -> dict:
    """The /v1/videos request: the agent's choices over the model card's defaults."""
    fields: dict = {"prompt": prompt, "seed": seed}
    size = getattr(defaults, "size", None)
    if size:
        w, h = (int(x) for x in size.lower().split("x"))
        long_side, short_side = max(w, h), min(w, h)
        size = (f"{short_side}x{long_side}" if aspect_ratio == "9:16"
                else f"{long_side}x{short_side}")
        fields["size"] = size
    fps = getattr(defaults, "fps", None)
    if fps:
        fields["fps"] = fps
    extra = dict(getattr(defaults, "extra_params", None) or {})
    frames = getattr(defaults, "num_frames", None)
    if frames:
        # Frame-count models (Wan VACE) need 4k+1 frames, so a requested length
        # becomes the nearest valid frame count at or below it.
        if seconds and fps:
            frames = max(5, (seconds * fps - 1) // 4 * 4 + 1)
        fields["num_frames"] = frames
    elif "duration" in extra:
        # MiniMax-H3 reads its clip length from extra_params.duration.
        extra["duration"] = float(seconds or getattr(defaults, "seconds", None) or extra["duration"])
    elif isinstance(extra.get("target"), dict):
        # SGLang's MiniMax-H3 takes length and shape only from
        # extra_params.target; seconds is echoed for the job's metadata.
        target = dict(extra["target"])
        length = seconds or getattr(defaults, "seconds", None) or target.get("duration_seconds")
        if length:
            target["duration_seconds"] = float(length)
            fields["seconds"] = int(length)
        target["aspect_ratio"] = aspect_ratio
        extra["target"] = target
    elif seconds := (seconds or getattr(defaults, "seconds", None)):
        fields["seconds"] = seconds
    if getattr(defaults, "send_aspect_ratio", False):
        fields["aspect_ratio"] = aspect_ratio
    guidance = getattr(defaults, "guidance_scale", None)
    if guidance is not None:
        fields["guidance_scale"] = guidance
    flow_shift = getattr(defaults, "flow_shift", None)
    if flow_shift is not None:
        fields["flow_shift"] = flow_shift
    if extra:
        fields["extra_params"] = extra
    if steps := (steps or getattr(defaults, "steps", None)):
        fields["num_inference_steps"] = steps
    return fields


def video_form() -> aiohttp.FormData:
    return aiohttp.FormData(default_to_multipart=True)


def takes_conditions_for(backend: "Backend") -> bool:
    """Same question as takes_conditions(), asked of the recipe's defaults.

    Needed before the request is built, to decide whether a clip and a picture
    may be given together.
    """
    extra = getattr(backend.defaults, "extra_params", None) or {}
    return isinstance(extra.get("conditions"), list)


def takes_conditions(fields: dict) -> bool:
    """Does this model read its reference from `extra_params.conditions`?

    MiniMax-H3 does, and only that: SGLang documents that the generic
    top-level upload fields "are not lowered into H3 reference conditions",
    so a multipart input_reference reaches it as nothing at all and a ref2va
    request is then rejected for having no reference. A recipe declares the
    contract by shipping `conditions` as a list in its video_defaults.
    """
    return isinstance((fields.get("extra_params") or {}).get("conditions"), list)


_MIMES = {"image": "image/png", "video": "video/mp4", "audio": "audio/wav"}


def with_condition(fields: dict, kind: str, raw: bytes, frame_index: int | None = None) -> dict:
    """Put one of the caller's pictures, clips or sounds into `extra_params.conditions`.

    The bytes travel as a base64 data URI, which SGLang materializes on its
    own side -- no shared volume between the Hub and the model's container,
    and no second fetch back out over the network.

    H3 reads a picture two different ways: a keyframe (frame_index 0 or -1)
    is reproduced as the clip's first or last frame, while a reference only
    guides identity, style and composition and may be recomposed or recropped.
    """
    extra = dict(fields.get("extra_params") or {})
    condition = {
        "type": kind,
        "uri": f"data:{_MIMES[kind]};base64,{base64.b64encode(raw).decode()}",
        "role": "reference" if frame_index is None else "keyframe",
    }
    if frame_index is not None:
        condition["frame_index"] = frame_index
    extra["conditions"] = [*extra["conditions"], condition]
    return {**fields, "extra_params": extra}


def clip_seconds(fields: dict) -> float | int | None:
    """The clip length a request asks for, however the model takes it."""
    if fields.get("seconds"):
        return fields["seconds"]
    if fields.get("num_frames") and fields.get("fps"):
        return round(fields["num_frames"] / fields["fps"], 1)
    duration = (fields.get("extra_params") or {}).get("duration")
    return duration or None


def _prune() -> None:
    while len(_jobs) >= MAX_JOBS:
        _jobs.pop(min(_jobs, key=lambda k: _jobs[k]["created"]))


async def start(*, prompt: str, image: str | None, seconds: int | None,
                aspect_ratio: str | None, seed: int | None, steps: int | None, model: str | None,
                video: str | None = None, last_image: str | None = None,
                images: list[str] | None = None, audio: str | None = None,
                user: dict | None = None) -> dict:
    images = [i for i in (images or []) if i]
    if images and not image:
        image, images = images[0], images[1:]
    needs = {kind for kind, given in (("image", image), ("video", video), ("images", images),
                                      ("last_image", last_image), ("audio", audio)) if given}
    kind = "video" if video else "image" if image or last_image else "audio" if audio else "text"
    backend = await pick_backend(needs or {"text"}, model)
    # A conditions model takes a LIST of references, so it can be given a clip
    # AND a picture at once -- which is the whole shape of a face swap: the
    # video whose face changes, plus the face to put there. Every other model
    # has one input slot and must still be told to pick one.
    if image and video and not takes_conditions_for(backend):
        raise ImageError("Give either a starting image or an input video, not both.")
    if aspect_ratio and aspect_ratio not in backend.aspect_ratios:
        raise ImageError(f"{backend.slug} renders {', '.join(backend.aspect_ratios)}, "
                         f"not {aspect_ratio}.")
    if seconds and seconds > backend.max_seconds:
        raise ImageError(f"{backend.slug} renders clips up to {backend.max_seconds} s.")
    task = (getattr(backend.defaults, "extra_params", None) or {}).get("task")
    # MiniMax-H3's FL2VA checkpoint serves two tasks: t2va from text alone and
    # fl2va, which pins the clip's first frame, last frame or both. A picture
    # turns the one into the other; the clip then takes the picture's shape
    # unless the caller asked for one.
    keyframes = task == "t2va" and bool(image or last_image)
    if keyframes:
        task = "fl2va"
    seed = seed if seed is not None else secrets.randbelow(MAX_SEED)
    fields = video_fields(backend.defaults, prompt=prompt, seconds=seconds,
                          aspect_ratio=aspect_ratio or ("auto" if keyframes else "16:9"),
                          seed=seed, steps=steps)
    if task and fields.get("extra_params", {}).get("task") != task:
        fields["extra_params"] = {**fields["extra_params"], "task": task}
    url = f"{proxy_service.internal_url(backend.slug)}/v1/videos"
    timeout = aiohttp.ClientTimeout(total=300, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        # (kind, bytes, frame_index): frame_index 0 / -1 pins the first / last
        # frame; None makes it a reference. Video first, then pictures, then
        # sound, so the prompt's <Video 1>, <Picture 1>, <Picture 2> and
        # <Audio 1> tags number the way the caller wrote them.
        references = []
        if video:
            references.append(("video", await load_input(video, session, user, suffix=".mp4",
                                                         max_bytes=MAX_VIDEO_INPUT_BYTES), None))
        for i, ref in enumerate([image, *images] if image else []):
            references.append(("image", to_png(await load_input(ref, session, user)),
                               0 if keyframes and i == 0 else None))
        if last_image:
            references.append(("image", to_png(await load_input(last_image, session, user)), -1))
        if audio:
            references.append(("audio", await load_input(audio, session, user, suffix=".wav",
                                                         max_bytes=MAX_AUDIO_INPUT_BYTES), None))
        conditions = takes_conditions(fields)
        if conditions:
            for kind_, raw, frame_index in references:
                fields = with_condition(fields, kind_, raw, frame_index)
            # JSON, not multipart. A conditions model carries its references
            # inline as data URIs, so there is no file part to send -- and
            # SGLang caps any single multipart part at 1024 KB, which one
            # photograph's worth of base64 blows straight through.
            body, kwargs = None, {"json": dict(fields)}
        else:
            # Two request shapes, because the models take their references two
            # different ways. A model with an `input_reference` upload slot needs
            # multipart, and it must be FORCED -- aiohttp sends a file-less form as
            # urlencoded, which SGLang reads as a JSON body and rejects with
            # "prompt: Field required". vLLM-Omni takes H3's first and last
            # frames as an ordered `input_references` list, with the frame each
            # one pins in extra_params.frame_indices.
            if keyframes:
                fields["extra_params"] = {**fields["extra_params"],
                                          "frame_indices": [f for _, _, f in references]}
                fields.pop("aspect_ratio", None)   # FL2VA follows the picture
            form = video_form()
            slot = "input_references" if len(references) > 1 else "input_reference"
            for kind_, raw, _ in references:
                ext = "png" if kind_ == "image" else "mp4"
                form.add_field(slot, raw, filename=f"input.{ext}", content_type=_MIMES[kind_])
            for key, value in fields.items():
                form.add_field(key, json.dumps(value) if isinstance(value, dict) else str(value))
            body, kwargs = form, {}
        async with session.post(url, data=body, headers=proxy_service.probe_headers(),
                                **kwargs) as r:
            if r.status != 200:
                raise ImageError(f"{backend.slug} refused the video job (HTTP {r.status}): "
                                 f"{(await r.text())[:500]}")
            remote = await r.json()

    _prune()
    job_id = secrets.token_hex(16)
    _jobs[job_id] = {
        "slug": backend.slug, "remote_id": remote["id"], "created": time.time(),
        "seed": seed, "size": fields.get("size") or remote.get("size"),
        "seconds": clip_seconds(fields) or remote.get("seconds"),
        "steps": fields.get("num_inference_steps"), "kind": kind,
        "user_id": user and user["id"],
    }
    return {"job_id": job_id, "model": backend.slug, "status": remote.get("status", "queued"),
            **_summary(_jobs[job_id])}


async def rendering_on(slug: str) -> int:
    """Video jobs `slug` is still working on, as its server reports them.

    Asked of the server rather than kept here, because a job nobody collects
    never learns locally that it finished.
    """
    open_jobs = [j for j in _jobs.values()
                 if j["slug"] == slug and not j.get("video_id") and not j.get("failed")]
    if not open_jobs:
        return 0
    busy = 0
    timeout = aiohttp.ClientTimeout(total=10, sock_connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for job in open_jobs:
            try:
                async with session.get(f"{proxy_service.internal_url(slug)}/v1/videos/{job['remote_id']}",
                                       headers=proxy_service.probe_headers()) as r:
                    state = (await r.json()).get("status") if r.status == 200 else None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                state = None
            busy += state in ("queued", "in_progress")
    return busy


def _summary(job: dict) -> dict:
    return {k: job.get(k) for k in ("seconds", "size", "seed", "steps")}


def poster_jpeg_b64(path: Path) -> str | None:
    """A still from half a second in, for agents that cannot play video."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "poster.jpg"
        try:
            subprocess.run([ffmpeg, "-loglevel", "error", "-y", "-ss", "0.5", "-i", str(path),
                            "-frames:v", "1", "-vf", "scale=512:-2", str(out)],
                           check=True, timeout=60)
        except (subprocess.SubprocessError, OSError):
            return None
        return base64.b64encode(out.read_bytes()).decode() if out.is_file() else None


async def check(job_id: str, wait: int = 0, on_progress=None, user: dict | None = None) -> dict:
    """The job's state, waiting up to `wait` seconds for it to finish."""
    job = _jobs.get(job_id)
    if not job or not owns_job(job, user):
        raise ImageError("Unknown job_id. Video jobs are forgotten when the Hub restarts; start a new one.")
    base = {"job_id": job_id, "model": job["slug"], **_summary(job)}
    if job.get("video_id"):
        return {**base, "status": "completed", "video_id": job["video_id"],
                "poster": job.get("poster"), "inference_time_s": job.get("inference_time_s")}

    deadline = time.time() + max(0, min(wait, MAX_WAIT))
    url = f"{proxy_service.internal_url(job['slug'])}/v1/videos/{job['remote_id']}"
    timeout = aiohttp.ClientTimeout(total=MAX_WAIT + 300, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            async with session.get(url, headers=proxy_service.probe_headers()) as r:
                if r.status == 404:
                    raise ImageError(f"{job['slug']} no longer has this job (was it restarted?).")
                if r.status != 200:
                    raise ImageError(f"{job['slug']} status check failed (HTTP {r.status}).")
                remote = await r.json()
            state = remote.get("status")
            if state == "completed":
                VIDEO_DIR.mkdir(parents=True, exist_ok=True)
                video_id = secrets.token_hex(16)
                path = VIDEO_DIR / f"{video_id}.mp4"
                async with session.get(f"{url}/content", headers=proxy_service.probe_headers()) as r:
                    if r.status != 200:
                        raise ImageError(f"Fetching the video from {job['slug']} failed (HTTP {r.status}).")
                    with open(path, "wb") as f:
                        async for chunk in r.content.iter_chunked(1 << 20):
                            f.write(chunk)
                job["video_id"] = video_id
                await media_store.record(f"{video_id}.mp4", job.get("user_id"), job["slug"])
                job["poster"] = await asyncio.to_thread(poster_jpeg_b64, path)
                job["inference_time_s"] = remote.get("inference_time_s")
                return {**base, "status": "completed", "video_id": video_id,
                        "poster": job["poster"], "inference_time_s": job["inference_time_s"]}
            if state == "failed":
                job["failed"] = True
                error = (remote.get("error") or {}).get("message") or "no detail given"
                return {**base, "status": "failed", "error": error}
            if time.time() >= deadline:
                return {**base, "status": state or "queued", "progress": remote.get("progress", 0)}
            if on_progress:
                on_progress(f"{state or 'queued'} {remote.get('progress', 0)}%")
            await asyncio.sleep(POLL_SECONDS)
