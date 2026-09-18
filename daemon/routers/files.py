"""My files: each account's generated media and uploads, to see and delete.

GET /api/media lists the caller's own files (media_store decides what is theirs), DELETE
/api/media/{name} removes one, and /api/media/{name}/thumb is a small JPEG so
the page does not pull a hundred full-size PNGs. The page itself is the Hub's
My files screen (frontend/src/pages/Files.jsx).
"""
from __future__ import annotations

import asyncio
import io
from collections import OrderedDict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image

from daemon.config import settings
from daemon.services import image_service, media_store

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
    return {
        "files": await media_store.list_files(user),
        "results_days": media_store.RESULT_TTL // media_store.DAY,
        "uploads_days": media_store.UPLOAD_TTL // media_store.DAY,
    }


@router.delete("/api/media/{name}")
async def delete_media(request: Request, name: str):
    if not await media_store.delete(name, _user(request)):
        return JSONResponse({"detail": "No such file."}, status_code=404)
    _thumbs.pop(name, None)
    return {"deleted": name}


def _make_thumb(path) -> bytes:
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((THUMB_EDGE, THUMB_EDGE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()


@router.get("/api/media/{name}/thumb")
async def media_thumb(request: Request, name: str):
    if not image_service.IMAGE_NAME_RE.match(name):
        return Response(status_code=404)
    path = image_service.hub_image_path(name)
    if path is None or not await media_store.can_read(name, _user(request)):
        return Response(status_code=404)
    if (jpeg := _thumbs.get(name)) is None:
        jpeg = await asyncio.to_thread(_make_thumb, path)
        _thumbs[name] = jpeg
        while len(_thumbs) > MAX_THUMBS:
            _thumbs.popitem(last=False)
    return Response(jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})
