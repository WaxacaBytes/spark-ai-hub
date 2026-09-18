"""The one-time upload link and the short-lived download link (link_service).

Both live outside /api, /v1 and /mcp on purpose: the request carries no Hub
session or key, so AuthMiddleware lets it through and the token in the path is
the only credential. It is checked here, and it can do exactly one thing.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from daemon.routers.uploads import receive
from daemon.services import link_service, media_store

router = APIRouter(tags=["links"])


@router.post(link_service.UPLOAD_PREFIX + "{token}")
async def upload_once(request: Request, token: str):
    owner = await link_service.claim_upload(token)
    if owner is None:
        return JSONResponse({"detail": "This upload link is used, expired or invalid. "
                             "Call create_upload for a new one."}, status_code=404)
    saved = False
    try:
        result = await receive(request, owner)
        saved = not isinstance(result, JSONResponse)
        return result
    finally:
        if not saved:                   # refused or cut off: the link stays usable
            await link_service.release_upload(token)


@router.get(link_service.DOWNLOAD_PREFIX + "{token}")
async def download(token: str):
    target = await link_service.download_target(token)
    if target is not None:
        name, owner = target
        path = media_store.find(name)
        if path is not None and await media_store.can_read(name, owner):
            return FileResponse(path, media_type=media_store.MEDIA_TYPES[path.suffix], filename=name,
                                headers={"Cache-Control": "no-store"})
    return JSONResponse({"detail": "This download link is expired or invalid, or the file "
                         "was deleted. Call create_download for a new one."}, status_code=404)
