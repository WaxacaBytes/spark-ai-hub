"""Who owns the media the Hub makes and stores, how long it keeps it, and the
listing behind each user's "My files" page (daemon/routers/files.py).

Results (/images/, /videos/, /audio/) and uploads are private to the account
that made them: the file routes and the tools that take a Hub URL as input
check the caller's Hub session or API key against the owner recorded here, and
answer 404 to anyone else, so another user's link reveals nothing. A file with
no owner row is nobody's to see (admins included) and just waits out its
lifetime; the files from before owners were recorded were given to the admin.

Nothing is kept for good. Results stay RESULT_TTL, long enough to come back to
a chat and refine one (clients working on local files save them next to their
files anyway); uploads are scratch inputs and go sooner. Age is the file's
mtime; the daemon's hourly janitor calls purge_expired().
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path

from daemon.config import settings
from daemon.db import get_db

DAY = 24 * 3600
RESULT_TTL = 30 * DAY
UPLOAD_TTL = 7 * DAY


async def record(name: str, user_id: int | None, model: str = "") -> None:
    """Note that account `user_id` made the file <name> (e.g. '<32 hex>.png')
    with the model `model` (a recipe slug; empty for an upload)."""
    if user_id is None:
        return
    db = await get_db()
    try:
        await db.execute("INSERT OR REPLACE INTO media (name, user_id, model) VALUES (?, ?, ?)",
                         (name, user_id, model))
        await db.commit()
    finally:
        await db.close()


async def can_read(name: str, user: dict | None) -> bool:
    if not settings.auth_enabled:
        return True
    if not user:
        return False
    db = await get_db()
    try:
        row = await (await db.execute("SELECT user_id FROM media WHERE name = ?", (name,))).fetchone()
    finally:
        await db.close()
    return _visible(row["user_id"] if row else None, user)


def _locations() -> list[tuple[Path, re.Pattern, str, str, bool]]:
    """(folder, name pattern, kind, public prefix, holds uploads) for every store."""
    # Imported here: image_service checks ownership through this module.
    from daemon.services import audio_service, image_service, video_service
    return [
        (image_service.IMAGE_DIR, image_service.IMAGE_NAME_RE, "image", image_service.PUBLIC_PREFIX, False),
        (image_service.UPLOAD_DIR, image_service.IMAGE_NAME_RE, "image", image_service.PUBLIC_PREFIX, True),
        (video_service.VIDEO_DIR, video_service.VIDEO_NAME_RE, "video", video_service.PUBLIC_PREFIX, False),
        (video_service.UPLOAD_DIR, video_service.VIDEO_NAME_RE, "video", video_service.PUBLIC_PREFIX, True),
        (audio_service.AUDIO_DIR, audio_service.AUDIO_NAME_RE, "audio", audio_service.PUBLIC_PREFIX, False),
    ]


def _entries() -> list[dict]:
    """Every stored result and upload, straight from disk."""
    entries = []
    for folder, name_re, kind, prefix, upload in _locations():
        if not folder.is_dir():
            continue
        for path in folder.iterdir():
            if not name_re.match(path.name):
                continue
            try:
                st = path.stat()
            except FileNotFoundError:
                continue
            if not path.is_file():
                continue
            created = int(st.st_mtime)
            entries.append({
                "name": path.name, "kind": kind, "upload": upload,
                "path": f"{prefix}/{path.name}", "bytes": st.st_size, "created": created,
                "expires_at": created + (UPLOAD_TTL if upload else RESULT_TTL),
                "_file": path,
            })
    return entries


MEDIA_TYPES = {".png": "image/png", ".mp4": "video/mp4", ".wav": "audio/wav"}
# A Hub media URL from any address the Hub is reached on (LAN, Tailscale,
# tunnel), so a file made through one can be used through another.
_URL_NAME_RE = re.compile(r"/(?:images|videos|audio)/([0-9a-f]{32}\.(?:png|mp4|wav))(?:$|[?#])")


def name_in_url(url: str) -> str | None:
    """'<id>.<ext>' for a Hub media URL, else None."""
    match = _URL_NAME_RE.search(url.strip())
    return match.group(1) if match else None


def find(name: str) -> Path | None:
    """The file behind a Hub media name ('<32 hex>.png' / .mp4 / .wav), result
    or upload, if any. Only names shaped like the Hub's own ever match."""
    for folder, name_re, _kind, _prefix, _upload in _locations():
        if name_re.match(name) and (folder / name).is_file():
            return folder / name
    return None


async def _rows() -> dict[str, dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT name, user_id, model FROM media")).fetchall()
    finally:
        await db.close()
    return {row["name"]: row for row in rows}


def _model_label(slug: str) -> str:
    """What the file's model is called on the page: the recipe's own name when
    the catalog still has it, else the slug it was made with."""
    if not slug:
        return ""
    # Imported here: the registry loads the recipes at startup.
    from daemon.services.registry_service import get_recipe
    recipe = get_recipe(slug)
    return recipe.name if recipe else slug


def _visible(owner: int | None, user: dict | None) -> bool:
    """The one rule for reading, listing and deleting a file."""
    if not settings.auth_enabled:
        return True
    if not user:
        return False
    return owner is not None and owner == user["id"]


async def list_files(user: dict | None) -> list[dict]:
    """`user`'s own files, newest first."""
    entries, rows = await asyncio.to_thread(_entries), await _rows()
    files = []
    for entry in entries:
        row = rows.get(entry["name"])
        if _visible(row["user_id"] if row else None, user):
            entry.pop("_file")
            entry["model"] = _model_label(row["model"] if row else "")
            files.append(entry)
    return sorted(files, key=lambda e: e["created"], reverse=True)


async def delete(name: str, user: dict | None) -> bool:
    """Delete one of `user`'s files. False if there is no such file they may see."""
    path = find(name)
    if path is None or not await can_read(name, user):
        return False
    path.unlink(missing_ok=True)
    db = await get_db()
    try:
        await db.execute("DELETE FROM media WHERE name = ?", (name,))
        await db.commit()
    finally:
        await db.close()
    return True


def _delete_old_files() -> list[str]:
    """Unlink results and uploads past their lifetime; returns their names."""
    now = time.time()
    old = [e for e in _entries() if e["expires_at"] < now]
    for entry in old:
        entry["_file"].unlink(missing_ok=True)
    return [e["name"] for e in old]


async def purge_expired() -> int:
    """Delete results and uploads past their lifetime. Returns how many went."""
    removed = await asyncio.to_thread(_delete_old_files)
    if removed:
        db = await get_db()
        try:
            await db.executemany("DELETE FROM media WHERE name = ?", [(n,) for n in removed])
            await db.commit()
        finally:
            await db.close()
    return len(removed)
