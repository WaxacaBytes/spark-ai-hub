"""Music generation behind the Hub's MCP server.

Music models are served on vLLM-Omni's OpenAI-style speech endpoint: POST
/v1/audio/speech with the lyrics in `input` and a style caption in
`instructions`, and the finished song comes back as a WAV in the response body.
A recipe opts in with the `openai-music` tag plus `text-to-music`.

Songs are stored in the Hub's data dir and served at /audio/<id>.wav, the same
capability-URL scheme as images and videos. They are never returned inline: a
30-second 32 kHz stereo WAV is ~11 MB, far too big for an agent's context.
"""
from __future__ import annotations

import io
import re
import secrets
import wave
from dataclasses import dataclass

import aiohttp

from daemon.config import settings
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services.image_service import ImageError, _app_base, _headers
from daemon.services.registry_service import get_recipes

AUDIO_DIR = settings.data_dir / "audio"
PUBLIC_PREFIX = "/audio"
AUDIO_NAME_RE = re.compile(r"^([0-9a-f]{32})\.wav$")

OPENAI_MUSIC_TAG = "openai-music"
FRAMES_PER_SECOND = 25        # MiniMax-Music3 audio frames; max_new_tokens counts these
MAX_SECONDS = 360
DEFAULT_SECONDS = 30
JOB_TIMEOUT = 60 * 60
MAX_SEED = 2**31 - 1


@dataclass(frozen=True)
class Backend:
    slug: str
    summary: str
    defaults: object | None = None


def backends() -> dict[str, Backend]:
    found: dict[str, Backend] = {}
    for slug, recipe in sorted(get_recipes().items()):
        if OPENAI_MUSIC_TAG in recipe.tags and "text-to-music" in recipe.tags:
            found[slug] = Backend(slug=slug, summary=recipe.name,
                                  defaults=getattr(recipe, "audio_defaults", None))
    return found


async def pick_backend(model: str | None) -> Backend:
    installed_slugs = await get_installed_slugs()
    installed = {slug: b for slug, b in backends().items() if slug in installed_slugs}
    candidates = list(installed.values())
    if model:
        backend = installed.get(model)
        if backend is None:
            names = ", ".join(b.slug for b in candidates) or "none installed"
            raise ImageError(f"'{model}' is not an installed music model. "
                             f"Installed music models: {names}.")
        candidates = [backend]
    starting = []
    for backend in candidates:
        if await is_recipe_running(backend.slug):
            if is_ready(backend.slug):
                return backend
            starting.append(backend.slug)
    if starting:
        raise ImageError(f"{starting[0]} is still starting (loading weights). Try again in a minute.")
    if not candidates:
        raise ImageError("No music model is installed on the Spark. "
                         "Install one from the Spark AI Hub first.")
    names = " or ".join(b.slug for b in candidates)
    raise ImageError(f"No music model is running on the Spark. Launch {names} "
                     f"from the Spark AI Hub first.")


def music_body(*, lyrics: str, style: str, seconds: int, seed: int) -> dict:
    seconds = max(5, min(int(seconds), MAX_SECONDS))
    return {
        "input": lyrics,
        "instructions": style,
        "seed": seed,
        # A cap, not a target: the model ends the song when it is done.
        "max_new_tokens": seconds * FRAMES_PER_SECOND,
        "response_format": "wav",
    }


def wav_seconds(raw: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(raw)) as w:
            return w.getnframes() / float(w.getframerate())
    except (wave.Error, EOFError):
        return None


async def generate(*, lyrics: str, style: str, seconds: int | None, seed: int | None,
                   model: str | None, on_progress=None) -> dict:
    backend = await pick_backend(model)
    seed = seed if seed is not None else secrets.randbelow(MAX_SEED)
    seconds = seconds or getattr(backend.defaults, "seconds", None) or DEFAULT_SECONDS
    if on_progress:
        on_progress(f"composing on {backend.slug}")
    timeout = aiohttp.ClientTimeout(total=JOB_TIMEOUT, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{_app_base(backend.slug)}/v1/audio/speech", headers=_headers(),
                                json=music_body(lyrics=lyrics, style=style,
                                                seconds=seconds, seed=seed)) as r:
            if r.status != 200:
                raise ImageError(f"{backend.slug} failed (HTTP {r.status}): {(await r.text())[:500]}")
            raw = await r.read()
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    audio_id = secrets.token_hex(16)
    (AUDIO_DIR / f"{audio_id}.wav").write_bytes(raw)
    length = wav_seconds(raw)
    return {"audio_id": audio_id, "model": backend.slug,
            "seconds": round(length, 1) if length else seconds, "seed": seed}
