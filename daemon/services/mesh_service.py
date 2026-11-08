"""3D generation behind the Hub's MCP server.

3D models are SGLang Diffusion's meshes API: POST /v1/meshes takes a picture
and queues a job, GET /v1/meshes/{id} reports it, and GET
/v1/meshes/{id}/content serves the textured GLB. A recipe opts in with the
`openai-meshes` tag plus `image-to-3d`; with `multi-view` it also takes the
object's `left`, `back` and `right` views next to the front picture (the
Hub's hy3dgen engine for Hunyuan3D-2mv speaks the same API with those fields). A model takes minutes, so it runs as a
Hub job like a song (image_service.start_job): generate_3d starts it, get_3d
collects it.

Most models read the object's outline from the picture's alpha channel and
cut nothing themselves: an opaque photo comes back as a slab with the photo
painted on. So a picture with no transparency has its background cut away
here first, by flood-filling the plain colour in from its edges. That fits
what agents make for them -- one object on a plain background -- and a
cut-out PNG is sent as it is. A picture whose background is not plain enough
is refused with what to do instead. A model tagged `background-removal`
(Pixal3D, with BRIA's RMBG-2.0) cuts any background out itself and gets the
pictures -- the front and any other views -- as they are.

Models are stored in the Hub's data dir and served at /meshes/<id>.glb, private
to the account that made them (media_store). The cut-out picture is kept beside
each as <id>.png: My files shows it as the model's thumbnail, and the agent
gets it as the preview.

An agent picks the file format it needs: GLB (the model as made), OBJ (zipped
with its .mtl and texture), STL (shape only, Z-up as slicers expect) or PLY
(shape with the texture sampled onto the vertices). Every engine returns a GLB,
and the others are written from it with trimesh -- the library those engines
save with themselves -- at /meshes/<id>.obj.zip, .stl and .ply, the first time
they are asked for. One case asks the model for more: STL has no colour, and a
model tagged `texture-optional` (Hi3D, Hunyuan3D-2mv) can skip painting and
return its full-detail shape (Hi3D paints a 200,000-face copy of a
1,000,000-face mesh), so an STL is built from that, and sooner.

A model tagged `gaussian-splat` (Idol) speaks the same API but makes a 3D
Gaussian splat, not a mesh: a standard 3DGS PLY, stored apart at
/splats/<id>.ply with its picture beside it. Its other format is SPZ
(Niantic's compressed splat file, about a fifth of the size), written from
the PLY the first time it is asked for; a splat has no surfaces, so there is
no GLB, OBJ or STL of it. Such a model also takes `mode` (a photo is framed
more loosely than a render) and the number of `gaussians`.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import io
import os
import re
import secrets
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import numpy as np
import trimesh
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from daemon.config import settings
from daemon.models.recipe import media_figures
from daemon.services import media_store, proxy_service
from daemon.services.docker_service import get_installed_slugs, is_ready, is_recipe_running
from daemon.services.image_service import IMAGE_DIR, ImageError, load_input, pick_ready, rendering, to_png
from daemon.services.registry_service import get_recipes

MESH_DIR = settings.data_dir / "meshes"
PUBLIC_PREFIX = "/meshes"
MESH_NAME_RE = re.compile(r"^([0-9a-f]{32})\.glb$")
# The other formats, written from the GLB beside it.
EXPORT_NAME_RE = re.compile(r"^([0-9a-f]{32})\.(obj\.zip|stl|ply)$")
EXTENSIONS = {"glb": ".glb", "obj": ".obj.zip", "stl": ".stl", "ply": ".ply"}
FORMAT_OF = {ext.lstrip("."): fmt for fmt, ext in EXTENSIONS.items()}
# Gaussian splats: the PLY as made, and the SPZ written from it.
SPLAT_DIR = settings.data_dir / "splats"
SPLAT_PREFIX = "/splats"
SPLAT_NAME_RE = re.compile(r"^([0-9a-f]{32})\.ply$")
SPLAT_EXPORT_RE = re.compile(r"^([0-9a-f]{32})\.spz$")
SPLAT_EXTENSIONS = {"ply": ".ply", "spz": ".spz"}
MIN_GAUSSIANS, MAX_GAUSSIANS = 32_768, 1_048_576

OPENAI_MESHES_TAG = "openai-meshes"
MULTI_VIEW_TAG = "multi-view"
OWN_CUTOUT_TAG = "background-removal"
TEXTURE_OPTIONAL_TAG = "texture-optional"
SPLAT_TAG = "gaussian-splat"
VIEWS = ("left", "back", "right")        # besides the front `image`
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
    multi_view: bool = False
    cuts_out: bool = False                   # removes backgrounds itself
    texture_optional: bool = False           # takes texture=false: the bare full-detail shape
    splat: bool = False                      # makes Gaussian splats (PLY), not meshes

    @property
    def formats(self) -> dict[str, str]:
        return SPLAT_EXTENSIONS if self.splat else EXTENSIONS


def backends() -> dict[str, Backend]:
    """Every 3D model the catalog offers: recipes tagged `openai-meshes` + `image-to-3d`."""
    return {slug: Backend(slug=slug, summary=recipe.name, multi_view=MULTI_VIEW_TAG in recipe.tags,
                          cuts_out=OWN_CUTOUT_TAG in recipe.tags,
                          texture_optional=TEXTURE_OPTIONAL_TAG in recipe.tags,
                          splat=SPLAT_TAG in recipe.tags)
            for slug, recipe in sorted(get_recipes().items())
            if OPENAI_MESHES_TAG in recipe.tags and "image-to-3d" in recipe.tags}


async def installed_backends() -> dict[str, Backend]:
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
            "accepts": ["image-to-3d"] + (["multi-view"] if backend.multi_view else []),
            **({"views": ["front (image)", *VIEWS]} if backend.multi_view else {}),
            "output": ("Gaussian splat, not a mesh: 3DGS PLY, or compact SPZ (generate_3d format); "
                       f"photo and gaussians ({MIN_GAUSSIANS:,}-{MAX_GAUSSIANS:,}) apply"
                       if backend.splat else "textured GLB; also OBJ, STL or PLY (generate_3d format)"),
            "background": ("any: the model cuts the object out itself" if backend.cuts_out
                           else "plain: the Hub cuts it away first"),
            "state": ("ready" if running and is_ready(slug)
                      else "starting" if running else "stopped"),
            "min_memory_gb": recipes[slug].requirements.min_memory_gb,
            **media_figures(recipes[slug]),
        })
    return out


async def pick_backend(model: str | None, multi_view: bool = False, fmt: str | None = None) -> Backend:
    """The model to run: `model`, or the first ready one that takes the views
    and makes the file format asked for (None: any)."""
    installed = await installed_backends()
    what = "3D model that takes several views" if multi_view else "3D model"
    candidates = [b for b in installed.values() if b.multi_view or not multi_view]
    if fmt and fmt not in EXTENSIONS and fmt not in SPLAT_EXTENSIONS:
        raise ImageError(f"Unknown format {fmt!r}: use one of {', '.join(EXTENSIONS | SPLAT_EXTENSIONS)}.")
    if model and (backend := installed.get(model)) and fmt and fmt not in backend.formats:
        kind = "Gaussian splats" if backend.splat else "meshes"
        raise ImageError(f"{model} makes {kind}: format {' or '.join(backend.formats)}.")
    if fmt:
        what += f" that makes {fmt.upper()}"
        candidates = [b for b in candidates if fmt in b.formats]
    if model:
        backend = installed.get(model)
        if backend is None or backend not in candidates:
            names = ", ".join(b.slug for b in candidates) or "none installed"
            raise ImageError(f"'{model}' is not an installed {what}. Installed: {names}.")
        candidates = [backend]
    return await pick_ready(candidates, what)


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
        img = img.convert("RGBA")
        img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE))
        canvas = Image.new("RGB", img.size, (128, 128, 128))
        canvas.paste(img, mask=img.getchannel("A"))
    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=85)
    return base64.b64encode(out.getvalue()).decode()


def made_here(image: str) -> bool:
    """Whether `image` is a picture the Hub's image tools made (not an upload
    or a picture from elsewhere): a render, as far as a splat model's framing
    goes, where anything else is taken for a photo."""
    name = media_store.name_in_url(image)
    path = media_store.find(name) if name else None
    return path is not None and path.parent == IMAGE_DIR


async def generate(*, image: str, seed: int | None, steps: int | None, model: str | None,
                   views: dict[str, str] | None = None, user: dict | None = None,
                   fmt: str | None = None, photo: bool | None = None,
                   gaussians: int | None = None) -> dict:
    started = time.monotonic()
    views = {k: v for k, v in (views or {}).items() if v}
    if unknown := set(views) - set(VIEWS):
        raise ImageError(f"Unknown view {', '.join(sorted(unknown))}: views are {', '.join(VIEWS)}.")
    backend = await pick_backend(model, multi_view=bool(views), fmt=fmt)
    fmt = fmt or next(iter(backend.formats))
    if gaussians is not None and not MIN_GAUSSIANS <= gaussians <= MAX_GAUSSIANS:
        raise ImageError(f"gaussians is {MIN_GAUSSIANS:,} to {MAX_GAUSSIANS:,}.")
    seed = seed if seed is not None else secrets.randbelow(MAX_SEED)
    base = f"{proxy_service.internal_url(backend.slug)}/v1/meshes"
    headers = proxy_service.probe_headers()
    timeout = aiohttp.ClientTimeout(total=JOB_TIMEOUT, sock_connect=10)
    with rendering(backend.slug):
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # The picture as the model gets it: cut out here, or as it is for a
            # model that removes backgrounds itself.
            raw = await load_input(image, session, user)
            cutout = await asyncio.to_thread(to_png if backend.cuts_out else cut_out, raw)
            form = aiohttp.FormData()
            form.add_field("image", cutout, filename="input.png", content_type="image/png")
            for name, ref in views.items():
                raw_view = await load_input(ref, session, user)
                view = await asyncio.to_thread(to_png if backend.cuts_out else cut_out, raw_view)
                form.add_field(name, view, filename=f"{name}.png", content_type="image/png")
            form.add_field("seed", str(seed))
            form.add_field("output_format", "ply" if backend.splat else "glb")
            if backend.splat:
                photo = not made_here(image) if photo is None else photo
                form.add_field("mode", "real" if photo else "synthetic")
                if gaussians:
                    form.add_field("gaussians", str(gaussians))
            # Shape only: skip the paint step where the model can, for its
            # full-detail mesh.
            untextured = fmt == "stl" and backend.texture_optional
            if untextured:
                form.add_field("texture", "false")
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
                made = await r.read()

    folder, master = (SPLAT_DIR, ".ply") if backend.splat else (MESH_DIR, ".glb")
    folder.mkdir(parents=True, exist_ok=True)
    mesh_id = secrets.token_hex(16)
    (folder / f"{mesh_id}.png").write_bytes(cutout)
    (folder / f"{mesh_id}{master}").write_bytes(made)
    if backend.formats[fmt] != master:
        await asyncio.to_thread(export_splat if backend.splat else export, mesh_id, fmt)
    took = time.monotonic() - started
    await media_store.record(f"{mesh_id}{master}", user and user["id"], backend.slug, took, seed=seed)
    kind = ({"splat": True, "photo": photo, "gaussians": remote.get("gaussians")} if backend.splat
            else {"textured": not untextured})
    return {"mesh_id": mesh_id, "model": backend.slug, "seed": seed, "steps": steps,
            "views": ["front", *views], "bytes": len(made), "took_s": round(took, 1),
            "format": fmt, **kind,
            "preview": await asyncio.to_thread(preview_jpeg_b64, cutout)}


def export(mesh_id: str, fmt: str) -> Path:
    """The model <mesh_id> as `fmt`, written from its GLB the first time it is
    asked for. Blocking: call it in a thread."""
    glb_path = MESH_DIR / f"{mesh_id}.glb"
    if fmt == "glb":
        return glb_path
    path = MESH_DIR / f"{mesh_id}{EXTENSIONS[fmt]}"
    if not path.is_file():
        data = {"obj": _obj_zip, "stl": _stl, "ply": _ply}[fmt](glb_path.read_bytes())
        partial = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
        partial.write_bytes(data)
        os.replace(partial, path)
    return path


def _parts(glb: bytes) -> list:
    """Each mesh in the GLB with its place in the scene applied."""
    return list(trimesh.load(io.BytesIO(glb), file_type="glb", force="scene").dump())


def _stl(glb: bytes) -> bytes:
    mesh = trimesh.util.concatenate([trimesh.Trimesh(p.vertices, p.faces, process=False)
                                     for p in _parts(glb)])
    # glTF is Y-up; slicers and CAD read STL as Z-up.
    mesh.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
    return mesh.export(file_type="stl")


def _ply(glb: bytes) -> bytes:
    parts = _parts(glb)
    for part in parts:
        if isinstance(part.visual, trimesh.visual.TextureVisuals):
            part.visual = part.visual.to_color()     # the texture, sampled at each vertex
    return trimesh.util.concatenate(parts).export(file_type="ply")


def _obj_zip(glb: bytes) -> bytes:
    obj, files = trimesh.exchange.obj.export_obj(trimesh.Scene(_parts(glb)), include_texture=True,
                                                 return_texture=True, mtl_name="model.mtl")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("model.obj", obj)
        for name, data in files.items():
            archive.writestr(name, data)
    return out.getvalue()


def export_splat(splat_id: str, fmt: str) -> Path:
    """The splat <splat_id> as `fmt`, written from its PLY the first time it
    is asked for. Blocking: call it in a thread."""
    ply_path = SPLAT_DIR / f"{splat_id}.ply"
    if fmt == "ply":
        return ply_path
    path = SPLAT_DIR / f"{splat_id}{SPLAT_EXTENSIONS[fmt]}"
    if not path.is_file():
        partial = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
        partial.write_bytes(_spz(ply_path.read_bytes()))
        os.replace(partial, path)
    return path


def _ply_columns(ply: bytes) -> dict[str, np.ndarray]:
    """The vertex properties of a binary 3DGS PLY, by name."""
    end = ply.index(b"end_header\n") + len(b"end_header\n")
    header = ply[:end].decode("ascii").splitlines()
    if "format binary_little_endian 1.0" not in header:
        raise ValueError("Not a binary little-endian PLY.")
    count = next(int(line.split()[2]) for line in header if line.startswith("element vertex "))
    names = [line.split()[2] for line in header if line.startswith("property ")]
    if any(line.split()[1] != "float" for line in header if line.startswith("property ")):
        raise ValueError("Only float properties are read.")
    rows = np.frombuffer(ply, dtype="<f4", count=count * len(names), offset=end).reshape(count, len(names))
    return {name: rows[:, i] for i, name in enumerate(names)}


def _spz(ply: bytes, fractional_bits: int = 16) -> bytes:
    """A 3DGS PLY (spherical-harmonics degree 0) as SPZ version 2, Niantic's
    format (github.com/nianticlabs/spz): a 16-byte header, then positions as
    24-bit fixed point, alphas, colours, log-scales and rotations as bytes,
    all gzipped. SPZ keeps its points right-up-back where a 3DGS PLY is
    right-down-front, so Y and Z turn over (a half turn about X), as spz's own
    converter does. 16 fractional bits: positions to 1/65,536 within +-128,
    ample for an object in a unit box."""
    c = _ply_columns(ply)
    n = len(c["x"])
    xyz = np.stack([c["x"], -c["y"], -c["z"]], axis=1)
    fixed = np.round(xyz * (1 << fractional_bits)).astype("<i4").reshape(-1, 1).view(np.uint8)
    positions = fixed.reshape(n * 3, 4)[:, :3]                  # the low 3 bytes, little-endian
    u8 = lambda v: np.clip(np.round(v), 0, 255).astype(np.uint8)
    alphas = u8(255 / (1 + np.exp(-c["opacity"])))
    colors = u8(np.stack([c[f"f_dc_{i}"] for i in range(3)], axis=1) * (0.15 * 255) + 0.5 * 255)
    scales = u8((np.stack([c[f"scale_{i}"] for i in range(3)], axis=1) + 10) * 16)
    # PLY quaternions are (w, x, y, z); the half turn negates y and z. SPZ v2
    # keeps x, y, z of the unit quaternion with w >= 0.
    q = np.stack([c["rot_0"], c["rot_1"], -c["rot_2"], -c["rot_3"]], axis=1)
    q /= np.linalg.norm(q, axis=1, keepdims=True)
    q *= np.where(q[:, :1] < 0, -1, 1)
    rotations = u8(q[:, 1:] * 127.5 + 127.5)
    header = np.array([0x5053474E, 2, n], dtype="<u4").tobytes() + bytes([0, fractional_bits, 0, 0])
    return gzip.compress(header + positions.tobytes() + alphas.tobytes() + colors.tobytes()
                         + scales.tobytes() + rotations.tobytes(), compresslevel=6)
