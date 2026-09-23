"""Files users upload so the image and video tools can work on them.

An MCP tool call carries JSON, not files, so a picture on someone's laptop has
no way into edit_image except as a URL. An upload gives it one: the file lands
next to the Hub's own results and gets the same kind of URL, /images/<id>.png
or /videos/<id>.mp4, which every tool already accepts. The daemon reads those
straight off its disk, so they work over plain http on the LAN — the
public-https rule only applies to URLs it has to fetch from elsewhere. Like
results, an upload is private to the account that sent it.

Uploads expire after media_store.UPLOAD_TTL (7 days): they are scratch
inputs, and whatever is made from one is a new result with its own lifetime.
"""
from __future__ import annotations

import io
import secrets
import time
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from daemon.services import image_service, media_store, video_service

MAX_IMAGE_BYTES = image_service.MAX_INPUT_BYTES
MAX_VIDEO_BYTES = video_service.MAX_VIDEO_INPUT_BYTES
MAX_BYTES = max(MAX_IMAGE_BYTES, MAX_VIDEO_BYTES)


# iPhone photos are HEIC; Pillow reads them once this is registered.
register_heif_opener()

# HEIF/HEIC and AVIF stills share MP4's container, so `ftyp` alone does not
# make a video: these brands mark an image.
IMAGE_BRANDS = {b"mif1", b"msf1", b"heic", b"heix", b"heim", b"heis",
                b"hevc", b"hevx", b"avif", b"avis"}


class UploadError(ValueError):
    pass


def _is_mp4(raw: bytes) -> bool:
    # ISO base media (MP4, MOV, M4V): the first box is `ftyp`, whose major
    # brand and compatible brands say what the file holds.
    if raw[4:8] != b"ftyp":
        return False
    size = int.from_bytes(raw[:4], "big")
    brands = raw[8:12], *(raw[i:i + 4] for i in range(16, min(size, 256), 4))
    return not IMAGE_BRANDS.intersection(brands)


def save(raw: bytes) -> dict:
    """Store an uploaded image or video; returns its kind, public path and expiry."""
    if not raw:
        raise UploadError("The upload is empty.")
    expires = int(time.time()) + media_store.UPLOAD_TTL
    if _is_mp4(raw):
        if len(raw) > MAX_VIDEO_BYTES:
            raise UploadError(f"Videos can be at most {MAX_VIDEO_BYTES // 2**20} MB.")
        video_id = secrets.token_hex(16)
        video_service.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        (video_service.UPLOAD_DIR / f"{video_id}.mp4").write_bytes(raw)
        return {"kind": "video", "id": video_id, "expires_at": expires,
                "path": f"{video_service.PUBLIC_PREFIX}/{video_id}.mp4"}

    if len(raw) > MAX_IMAGE_BYTES:
        raise UploadError(f"Images can be at most {MAX_IMAGE_BYTES // 2**20} MB.")
    try:
        with Image.open(io.BytesIO(raw)) as img:
            # Stored as the PNG the models are sent anyway, upright per EXIF.
            # Re-encoding also drops the EXIF itself: phone photos carry GPS.
            img = ImageOps.exif_transpose(img).convert("RGB")
            image_id = secrets.token_hex(16)
            image_service.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            img.save(image_service.UPLOAD_DIR / f"{image_id}.png", format="PNG")
            width, height = img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise UploadError("Not an image or MP4/MOV video the Hub can read.") from None
    return {"kind": "image", "id": image_id, "expires_at": expires,
            "width": width, "height": height,
            "path": f"{image_service.PUBLIC_PREFIX}/{image_id}.png"}


def is_upload(path: Path) -> bool:
    return path.parent in (image_service.UPLOAD_DIR, video_service.UPLOAD_DIR)

