"""My files: each account's generated media and uploads, to see and delete.

GET /api/media lists the caller's own files (media_store decides what is theirs), DELETE
/api/media/{name} removes one, and /api/media/{name}/thumb is a small JPEG so
the page does not pull a hundred full-size PNGs. A 3D model's is a still of
the model, which the page draws and PUTs to the same path the first time it
lists the model (the daemon has no renderer); until then it is the picture the
model was made from. The page itself is the Hub's My files screen
(frontend/src/pages/Files.jsx).
"""
from __future__ import annotations

import asyncio
import io
from collections import OrderedDict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image

from daemon.config import settings
from daemon.services import media_store

router = APIRouter(tags=["files"])

THUMB_EDGE = 320
_thumbs: OrderedDict[str, bytes] = OrderedDict()   # name -> JPEG, newest last
MAX_THUMBS = 500                                    # ~20 KB each


def _user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


@router.get("/api/media")
async def list_media(request: Request):
    user = _user(request)
    if user is None and settings.auth_enabled:     # AuthMiddleware guards /api too
        return JSONResponse({"detail": "Authentication required."}, status_code=401)
    return {"files": await media_store.list_files(user)}


@router.delete("/api/media/{name}")
async def delete_media(request: Request, name: str):
    if not await media_store.delete(name, _user(request)):
        return JSONResponse({"detail": "No such file."}, status_code=404)
    _thumbs.pop(name, None)
    return {"deleted": name}


def _make_thumb(path) -> bytes:
    with Image.open(path) as img:
        # A cut-out (a 3D model's picture, a transparent upload) on light
        # grey, not on whatever colour sits under its transparent pixels.
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (236, 236, 236))
        flat.paste(img, mask=img.getchannel("A"))
        img = flat
        img.thumbnail((THUMB_EDGE, THUMB_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


MAX_RENDER_BYTES = 8 * 1024 * 1024
MAX_RENDER_EDGE = 2048


def _save_render(raw: bytes, path) -> None:
    with Image.open(io.BytesIO(raw)) as img:
        if max(img.size) > MAX_RENDER_EDGE:
            raise ValueError("too large")
        img.convert("RGBA").save(media_store.render_of(path), format="PNG")


@router.put("/api/media/{name}/thumb")
async def set_render(request: Request, name: str):
    """A browser's still of one of its account's 3D models, to stand for it
    instead of the picture it was made from (lib/mesh3d.js snapshot)."""
    path = media_store.find(name) if name.endswith(".glb") else None
    if path is None or not await media_store.can_read(name, _user(request)):
        return Response(status_code=404)
    raw = await request.body()
    if not raw or len(raw) > MAX_RENDER_BYTES:
        return JSONResponse({"detail": "Send one PNG of the model."}, status_code=400)
    try:
        await asyncio.to_thread(_save_render, raw, path)
    except Exception:  # noqa: BLE001 - PIL raises many types
        return JSONResponse({"detail": "Not a readable image of a sensible size."}, status_code=400)
    _thumbs.pop(name, None)
    return {"rendered": name}


@router.get("/api/media/{name}/thumb")
async def media_thumb(request: Request, name: str):
    path = media_store.find(name) if name.endswith((".png", ".glb")) else None
    if path is None or not await media_store.can_read(name, _user(request)):
        return Response(status_code=404)
    if (jpeg := _thumbs.get(name)) is None:
        jpeg = await asyncio.to_thread(_make_thumb, media_store.picture(path))
        _thumbs[name] = jpeg
        while len(_thumbs) > MAX_THUMBS:
            _thumbs.popitem(last=False)
    return Response(jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})
