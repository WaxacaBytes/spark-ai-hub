"""Upload an image or video, get a Hub URL the MCP tools accept.

POST /api/uploads takes the file as the raw request body (guarded like the rest
of /api: a Hub session or API key), which keeps it to one curl flag:

    curl -H "Authorization: Bearer $KEY" --data-binary @photo.jpg $HUB/api/uploads

The Hub's My files screen (/files, and /upload which lands there) is the way in
from a browser, for when the chat is Claude on the web or a phone, where the
model can see a picture but cannot pass it on. The upload belongs to the
signed-in account, like everything in media_store.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from daemon.services import media_store, upload_service
from daemon.services.connect_service import request_origin

router = APIRouter(tags=["uploads"])


@router.post("/api/uploads")
async def upload(request: Request):
    return await receive(request, getattr(request.state, "user", None))


async def receive(request: Request, user: dict | None) -> dict | JSONResponse:
    """Read the body as one image or video and store it as `user`'s upload.

    Shared with the one-time upload links (routers/links.py). Returns the
    upload's info with its Hub URL, or the error response to send.
    """
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > upload_service.MAX_BYTES:
            return JSONResponse({"detail": f"Uploads can be at most "
                                 f"{upload_service.MAX_BYTES // 2**20} MB."}, status_code=413)
        chunks.append(chunk)
    try:
        info = await asyncio.to_thread(upload_service.save, b"".join(chunks))
    except upload_service.UploadError as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    await media_store.record(info["path"].rsplit("/", 1)[1], user and user["id"])
    origin = request_origin(request) or f"{request.url.scheme}://{request.url.netloc}"
    info["url"] = origin + info.pop("path")
    return info
