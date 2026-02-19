"""
ComfyUI custom node that adds a server-side model download API endpoint.
Place in custom_nodes/ directory.

Endpoints:
  POST /api/download-model  {url, directory, filename}
  GET  /api/download-model/status
"""

import os
import asyncio
import aiohttp
from aiohttp import web
import folder_paths
import server

_downloads = {}
_download_tasks = {}


class DownloadTracker:
    def __init__(self, url, filepath, filename):
        self.url = url
        self.filepath = filepath
        self.filename = filename
        self.progress = 0
        self.total = 0
        self.status = "pending"
        self.error = None
        self.cancel_requested = False


async def download_file(key, tracker):
    tracker.status = "downloading"
    tmp_path = tracker.filepath + ".tmp"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(tracker.url) as resp:
                if resp.status != 200:
                    tracker.status = "error"
                    tracker.error = f"HTTP {resp.status}"
                    return
                tracker.total = int(resp.headers.get("content-length", 0))
                tracker.progress = 0
                with open(tmp_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):
                        if tracker.cancel_requested:
                            tracker.status = "canceled"
                            if os.path.exists(tmp_path):
                                os.remove(tmp_path)
                            return
                        f.write(chunk)
                        tracker.progress += len(chunk)
        if tracker.cancel_requested:
            tracker.status = "canceled"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            return
        os.rename(tmp_path, tracker.filepath)
        tracker.status = "completed"
    except asyncio.CancelledError:
        tracker.status = "canceled"
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except Exception as e:
        tracker.status = "error"
        tracker.error = str(e)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    finally:
        _download_tasks.pop(key, None)


ALLOWED_SOURCES = [
    "https://civitai.com/",
    "https://huggingface.co/",
    "http://localhost:",
    "https://github.com/",
]

ALLOWED_SUFFIXES = [".safetensors", ".sft", ".ckpt", ".pth", ".bin", ".gguf"]


@server.PromptServer.instance.routes.post("/api/download-model")
async def download_model(request):
    data = await request.json()
    url = data.get("url", "")
    directory = data.get("directory", "")
    filename = data.get("filename", "")

    if not url or not filename:
        return web.json_response({"error": "url and filename required"}, status=400)

    if not any(url.startswith(src) for src in ALLOWED_SOURCES):
        return web.json_response(
            {"error": f"Download not allowed from this source"}, status=403
        )

    if not any(filename.endswith(s) for s in ALLOWED_SUFFIXES):
        return web.json_response({"error": f"File type not allowed"}, status=403)

    # Resolve the target directory
    if directory and directory in folder_paths.folder_names_and_paths:
        target_dir = folder_paths.folder_names_and_paths[directory][0][0]
    else:
        target_dir = folder_paths.folder_names_and_paths.get(
            "checkpoints", [[""]]
        )[0][0]

    filepath = os.path.join(target_dir, filename)

    if os.path.exists(filepath):
        return web.json_response({"status": "already_exists", "path": filepath})

    key = f"{directory}/{filename}"
    existing = _downloads.get(key)
    if existing and existing.status in ("pending", "downloading"):
        return web.json_response({"status": "already_downloading", "key": key})

    tracker = DownloadTracker(url, filepath, filename)
    _downloads[key] = tracker

    _download_tasks[key] = asyncio.create_task(download_file(key, tracker))

    return web.json_response({"status": "started", "key": key})


@server.PromptServer.instance.routes.get("/api/download-model/status")
async def download_status(request):
    result = {}
    for key, t in _downloads.items():
        result[key] = {
            "status": t.status,
            "progress": t.progress,
            "total": t.total,
            "error": t.error,
            "filename": t.filename,
        }
    return web.json_response(result)


@server.PromptServer.instance.routes.post("/api/download-model/cancel")
async def download_cancel(request):
    data = await request.json()
    key = data.get("key", "")
    if not key:
        return web.json_response({"error": "key required"}, status=400)

    tracker = _downloads.get(key)
    if tracker is None:
        return web.json_response({"error": "download not found"}, status=404)

    if tracker.status in ("completed", "error", "canceled"):
        return web.json_response({"status": tracker.status, "key": key})

    tracker.cancel_requested = True
    task = _download_tasks.get(key)
    if task and not task.done():
        task.cancel()

    return web.json_response({"status": "canceling", "key": key})


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
