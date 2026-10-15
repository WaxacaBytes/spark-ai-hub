"""3D generation behind the Hub's MCP server.

3D models are SGLang Diffusion's meshes API: POST /v1/meshes takes a picture
and queues a job, GET /v1/meshes/{id} reports it, and GET
/v1/meshes/{id}/content serves the textured GLB. A recipe opts in with the
`openai-meshes` tag plus `image-to-3d`. A model takes minutes, so it runs as a
Hub job like a song (image_service.start_job): generate_3d starts it, get_3d
collects it.

The model reads the object's outline from the picture's alpha channel and cuts
nothing itself: an opaque photo comes back as a slab with the photo painted on.
So a picture with no transparency has its background cut away here first, by
flood-filling the plain colour in from its edges. That fits what agents make
for it -- one object on a plain background -- and a cut-out PNG is sent as it
is. A picture whose background is not plain enough is refused with what to do
instead.

Models are stored in the Hub's data dir and served at /meshes/<id>.glb, private
to the account that made them (media_store). The cut-out picture is kept beside
each as <id>.png: My files shows it as the model's thumbnail, and the agent
gets it as the preview.
"""
from __future__ import annotations

import asyncio
import base64
import io
import re
import secrets
import time
from dataclasses import dataclass

import aiohttp
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from daemon.config import settings
from daemon.services import media_store, proxy_service
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services.image_service import ImageError, load_input, pick_ready, rendering
from daemon.services.registry_service import get_recipes

MESH_DIR = settings.data_dir / "meshes"
PUBLIC_PREFIX = "/meshes"
MESH_NAME_RE = re.compile(r"^([0-9a-f]{32})\.glb$")

OPENAI_MESHES_TAG = "openai-meshes"
POLL_SECONDS = 3
JOB_TIMEOUT = 30 * 60
MAX_SEED = 2**31 - 1
PREVIEW_EDGE = 512

# How far a pixel may stray from the background colour it is flood-filled
# from (PIL sums the difference over R, G and B), and how much of the picture
# the background must be for the cut to be believed.
CUT_TOLERANCE = 35
EDGE_STEP = 8
MIN_BACKGROUND, MAX_BACKGROUND = 0.05, 0.97


@dataclass(frozen=True)
class Backend:
    slug: str
    summary: str


def backends() -> dict[str, Backend]:
    """Every 3D model the catalog offers: recipes tagged `openai-meshes` + `image-to-3d`."""
    return {slug: Backend(slug=slug, summary=recipe.name)
            for slug, recipe in sorted(get_recipes().items())
            if OPENAI_MESHES_TAG in recipe.tags and "image-to-3d" in recipe.tags}


async def installed_backends() -> dict[str, Backend]:
    installed = await get_installed_slugs()
    return {slug: b for slug, b in backends().items() if slug in installed}


async def list_models() -> list[dict]:
    recipes = get_recipes()
    out = []
    for slug in await installed_backends():
        running = await is_recipe_running(slug)
        out.append({
            "model": slug,
            "name": recipes[slug].name,
            "accepts": ["image-to-3d"],
            "output": "GLB with textures",
            "state": ("ready" if running and is_ready(slug)
                      else "starting" if running else "stopped"),
            "min_memory_gb": recipes[slug].requirements.min_memory_gb,
        })
    return out


async def pick_backend(model: str | None) -> Backend:
    installed = await installed_backends()
    candidates = list(installed.values())
    if model:
        if model not in installed:
            names = ", ".join(installed) or "none installed"
            raise ImageError(f"'{model}' is not an installed 3D model. Installed 3D models: {names}.")
        candidates = [installed[model]]
    return await pick_ready(candidates, "3D model")


def _png(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def cut_out(raw: bytes) -> bytes:
    """The picture as an RGBA PNG whose background is transparent."""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            img = ImageOps.exif_transpose(img)
            rgba = img.convert("RGBA")
    except Exception as exc:  # noqa: BLE001 - PIL raises many types
        raise ImageError(f"Not a readable image: {exc}") from None
    w, h = rgba.size
    # Already cut out: enough of it is see-through. Not merely an alpha
    # channel -- Qwen-Image saves opaque pictures as RGBA with alpha 253-255.
    if sum(rgba.getchannel("A").histogram()[:128]) >= MIN_BACKGROUND * w * h:
        return _png(rgba)

    rgb = rgba.convert("RGB")
    filled = rgb.copy()
    sentinel = (255, 0, 255)
    edge = ([(x, 0) for x in range(0, w, EDGE_STEP)] + [(x, h - 1) for x in range(0, w, EDGE_STEP)]
            + [(0, y) for y in range(0, h, EDGE_STEP)] + [(w - 1, y) for y in range(0, h, EDGE_STEP)])
    for xy in edge:
        if filled.getpixel(xy) != sentinel:
            ImageDraw.floodfill(filled, xy, sentinel, thresh=CUT_TOLERANCE)
    # Background = what the fill changed. The 1 px erosion drops the halo of
    # blended edge pixels the fill stopped short of.
    changed = ImageChops.difference(rgb, filled).convert("L").point(lambda v: 255 if v else 0)
    alpha = ImageChops.invert(changed).filter(ImageFilter.MinFilter(3))
    share = changed.histogram()[255] / (w * h)
    if not MIN_BACKGROUND <= share <= MAX_BACKGROUND:
        raise ImageError(
            "This picture has no plain background to cut the object out of. Give one object on a "
            "plain background (generate_image can make one: ask for it \"centered on a plain white "
            "background\"), or a PNG whose background is already transparent.")
    rgba.putalpha(alpha)
    return _png(rgba)


def preview_jpeg_b64(cutout: bytes) -> str:
    """The cut-out on neutral grey, small, for agents that show pictures."""
    with Image.open(io.BytesIO(cutout)) as img:
        img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
        canvas = Image.new("RGB", img.size, (128, 128, 128))
        canvas.paste(img, mask=img.getchannel("A"))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode()


async def generate(*, image: str, seed: int | None, steps: int | None, model: str | None,
                   user: dict | None = None) -> dict:
    started = time.monotonic()
    backend = await pick_backend(model)
    seed = seed if seed is not None else secrets.randbelow(MAX_SEED)
    base = f"{proxy_service.internal_url(backend.slug)}/v1/meshes"
    headers = proxy_service.probe_headers()
    timeout = aiohttp.ClientTimeout(total=JOB_TIMEOUT, sock_connect=10)
    with rendering(backend.slug):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            cutout = await asyncio.to_thread(cut_out, await load_input(image, session, user))
            form = aiohttp.FormData()
            form.add_field("image", cutout, filename="input.png", content_type="image/png")
            form.add_field("seed", str(seed))
            form.add_field("output_format", "glb")
            if steps:
                form.add_field("num_inference_steps", str(steps))
            async with session.post(base, data=form, headers=headers) as r:
                if r.status != 200:
                    raise ImageError(f"{backend.slug} refused the 3D job (HTTP {r.status}): "
                                     f"{(await r.text())[:500]}")
                remote = await r.json()
            url = f"{base}/{remote['id']}"
            while remote.get("status") != "completed":
                if remote.get("status") == "failed":
                    error = (remote.get("error") or {}).get("message") or "no detail given"
                    raise ImageError(f"{backend.slug} failed to make the 3D model: {error}")
                await asyncio.sleep(POLL_SECONDS)
                async with session.get(url, headers=headers) as r:
                    if r.status != 200:
                        raise ImageError(f"{backend.slug} lost the 3D job (HTTP {r.status}); "
                                         "was it restarted?")
                    remote = await r.json()
            async with session.get(f"{url}/content", headers=headers) as r:
                if r.status != 200:
                    raise ImageError(f"Fetching the model from {backend.slug} failed (HTTP {r.status}).")
                glb = await r.read()

    MESH_DIR.mkdir(parents=True, exist_ok=True)
    mesh_id = secrets.token_hex(16)
    (MESH_DIR / f"{mesh_id}.png").write_bytes(cutout)
    (MESH_DIR / f"{mesh_id}.glb").write_bytes(glb)
    await media_store.record(f"{mesh_id}.glb", user and user["id"], backend.slug,
                             time.monotonic() - started)
    return {"mesh_id": mesh_id, "model": backend.slug, "seed": seed, "steps": steps,
            "bytes": len(glb), "inference_time_s": remote.get("inference_time_s"),
            "preview": await asyncio.to_thread(preview_jpeg_b64, cutout)}
