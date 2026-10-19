"""Who owns the media the Hub makes and stores, how long it keeps it, the
listing behind each user's "My files" page (daemon/routers/files.py), and how
much space each account takes (the admin's Users page).

Results (/images/, /videos/, /audio/) and uploads are private to the account
that made them: the file routes and the tools that take a Hub URL as input
check the caller's Hub session or API key against the owner recorded here, and
answer 404 to anyone else, so another user's link reveals nothing. A file with
no owner row (its account was deleted, or it predates owners being recorded)
is the admins'.

Results and uploads stay until their owner (or an admin) deletes them.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path

from daemon.config import settings
from daemon.db import get_db


async def record(name: str, user_id: int | None, model: str = "",
                 seconds: float | None = None, lora: str = "") -> None:
    """Note that account `user_id` made the file <name> (e.g. '<32 hex>.png')
    with the model `model` (a recipe slug; empty for an upload) running the
    LoRA `lora` (a LoRA recipe's slug; empty for none), and that making it
    took `seconds`, from the request to the finished file."""
    if user_id is None:
        return
    db = await get_db()
    try:
        await db.execute("INSERT OR REPLACE INTO media (name, user_id, model, seconds, lora) "
                         "VALUES (?, ?, ?, ?, ?)", (name, user_id, model, seconds, lora or ""))
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
    from daemon.services import audio_service, image_service, mesh_service, video_service
    return [
        (image_service.IMAGE_DIR, image_service.IMAGE_NAME_RE, "image", image_service.PUBLIC_PREFIX, False),
        (image_service.UPLOAD_DIR, image_service.IMAGE_NAME_RE, "image", image_service.PUBLIC_PREFIX, True),
        (video_service.VIDEO_DIR, video_service.VIDEO_NAME_RE, "video", video_service.PUBLIC_PREFIX, False),
        (video_service.UPLOAD_DIR, video_service.VIDEO_NAME_RE, "video", video_service.PUBLIC_PREFIX, True),
        (audio_service.AUDIO_DIR, audio_service.AUDIO_NAME_RE, "audio", audio_service.PUBLIC_PREFIX, False),
        (audio_service.UPLOAD_DIR, audio_service.AUDIO_NAME_RE, "audio", audio_service.PUBLIC_PREFIX, True),
        (mesh_service.MESH_DIR, mesh_service.MESH_NAME_RE, "mesh", mesh_service.PUBLIC_PREFIX, False),
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
                "_file": path,
            })
    return entries


MEDIA_TYPES = {".png": "image/png", ".mp4": "video/mp4", ".wav": "audio/wav",
               ".glb": "model/gltf-binary"}
# A Hub media URL from any address the Hub is reached on (LAN, Tailscale,
# tunnel), so a file made through one can be used through another.
_URL_NAME_RE = re.compile(r"/(?:images|videos|audio|meshes)/([0-9a-f]{32}\.(?:png|mp4|wav|glb))(?:$|[?#])")


def name_in_url(url: str) -> str | None:
    """'<id>.<ext>' for a Hub media URL, else None."""
    match = _URL_NAME_RE.search(url.strip())
    return match.group(1) if match else None


def render_of(path: Path) -> Path:
    """Where a 3D model's render is kept: a still of the model itself, drawn by
    the first browser to list it (My files), since the daemon cannot draw one."""
    return path.with_suffix(".render.png")


def picture(path: Path) -> Path:
    """The still that stands for a file: itself, or for a 3D model its render,
    or until there is one the cut-out picture it was made from (mesh_service)."""
    if path.suffix != ".glb":
        return path
    render = render_of(path)
    return render if render.is_file() else path.with_suffix(".png")


def find(name: str) -> Path | None:
    """The file behind a Hub media name ('<32 hex>.png' / .mp4 / .wav / .glb), result
    or upload, if any. Only names shaped like the Hub's own ever match."""
    for folder, name_re, _kind, _prefix, _upload in _locations():
        if name_re.match(name) and (folder / name).is_file():
            return folder / name
    return None


async def _rows() -> dict[str, dict]:
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT name, user_id, model, seconds, lora FROM media")).fetchall()
    finally:
        await db.close()
    return {row["name"]: row for row in rows}


def build_label(recipe) -> str:
    """A recipe's name with what tells its builds apart -- the part of the
    checkpoint it serves and its quantization -- added where the name leaves
    them out: a "MiniMax-H3 FP8" serving FL2VA reads "MiniMax-H3 FL2VA FP8"."""
    words = recipe.name.split()
    have = {w.lower() for w in words}
    if recipe.variant and recipe.variant.lower() not in have:
        words.insert(1, recipe.variant)
    if recipe.quantization and recipe.quantization.lower() not in have:
        words.append(recipe.quantization)
    return " ".join(words)


def _model_fields(slug: str, lora: str = "") -> dict:
    """What the page shows of the file's model: the build it was made with
    (build_label, or the slug when the catalog no longer has the recipe), the
    LoRA loaded into it, and whether its videos are 360° orbits to drag around."""
    if not slug:
        return {"model": "", "orbit": False}
    # Imported here: the registry loads the recipes at startup.
    from daemon.services.registry_service import get_recipe
    from daemon.services.video_service import ORBIT_TAG
    recipe = get_recipe(slug)
    # A stack of LoRAs is recorded as their slugs joined by "+".
    adapters = [(part, get_recipe(part)) for part in lora.split("+") if part]
    label = build_label(recipe) if recipe else slug
    base = label.split()
    for part, adapter in adapters:
        # "MiniMax-H3 Ref2VA Character Swap LoRA" on "MiniMax-H3 Ref2VA INT8 Turbo":
        # the words the base already says are not said twice.
        name = (adapter.name if adapter else part).split()
        shared = 0
        while shared < min(len(name) - 1, len(base)) and name[shared] == base[shared]:
            shared += 1
        label = f"{label} + {' '.join(name[shared:])}"
    orbit = any(r is not None and ORBIT_TAG in r.tags for r in (recipe, *(a for _, a in adapters)))
    return {"model": label, "orbit": orbit}


def _visible(owner: int | None, user: dict | None) -> bool:
    """The one rule for reading, listing and deleting a file."""
    if not settings.auth_enabled:
        return True
    if not user:
        return False
    return owner == user["id"] if owner is not None else user.get("role") == "admin"


async def list_files(user: dict | None) -> list[dict]:
    """`user`'s own files, newest first."""
    entries, rows = await asyncio.to_thread(_entries), await _rows()
    files = []
    for entry in entries:
        row = rows.get(entry["name"])
        if _visible(row["user_id"] if row else None, user):
            path = entry["_file"]
            entry.update(_model_fields(row["model"], row["lora"]) if row else _model_fields(""))
            entry["seconds"] = round(row["seconds"], 1) if row and row["seconds"] is not None else None
            if entry["kind"] == "mesh":
                entry["rendered"] = render_of(path).is_file()
            files.append(entry)
    shapes = await asyncio.gather(*(asyncio.to_thread(_shape, f.pop("_file"), f["bytes"]) for f in files))
    for entry, shape in zip(files, shapes):
        entry.update(shape)
    return sorted(files, key=lambda e: e["created"], reverse=True)


_shapes: dict[tuple[str, int], dict] = {}   # (path, bytes) -> shape; the files never change


def _shape(path: Path, size: int) -> dict:
    """What a file is, so the time it took reads against it: a picture's
    `width` and `height`, a clip's too plus its `duration` in seconds, a
    song's `duration`. Empty for a 3D model, or when it cannot be read."""
    key = (str(path), size)
    if key in _shapes:
        return _shapes[key]
    shape: dict = {}
    try:
        if path.suffix == ".png":
            from PIL import Image
            with Image.open(path) as img:
                shape = {"width": img.width, "height": img.height}
        elif path.suffix in (".mp4", ".wav") and (ffprobe := shutil.which("ffprobe")):
            out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "v:0",
                                  "-show_entries", "stream=width,height:format=duration",
                                  "-of", "json", str(path)],
                                 capture_output=True, timeout=10, check=True).stdout
            info = json.loads(out)
            stream = (info.get("streams") or [{}])[0]
            if stream.get("width"):
                shape = {"width": stream["width"], "height": stream["height"]}
            if duration := (info.get("format") or {}).get("duration"):
                shape["duration"] = round(float(duration), 2)
    except Exception:  # noqa: BLE001 - a broken file still lists, just without its shape
        shape = {}
    _shapes[key] = shape
    return shape


async def space_by_user() -> dict[int | None, dict]:
    """{"bytes", "files"} each account's results and uploads take, keyed by
    user id; None holds the ownerless files (the admins')."""
    entries, rows = await asyncio.to_thread(_entries), await _rows()
    space: dict[int | None, dict] = {}
    for entry in entries:
        row = rows.get(entry["name"])
        used = space.setdefault(row["user_id"] if row else None, {"bytes": 0, "files": 0})
        used["bytes"] += entry["bytes"]
        used["files"] += 1
    return space


async def delete(name: str, user: dict | None) -> bool:
    """Delete one of `user`'s files. False if there is no such file they may see."""
    path = find(name)
    if path is None or not await can_read(name, user):
        return False
    path.unlink(missing_ok=True)
    if path.suffix == ".glb":                   # its cut-out and its render
        path.with_suffix(".png").unlink(missing_ok=True)
        render_of(path).unlink(missing_ok=True)
    db = await get_db()
    try:
        await db.execute("DELETE FROM media WHERE name = ?", (name,))
        await db.commit()
    finally:
        await db.close()
    return True

