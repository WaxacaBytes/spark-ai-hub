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


class DownloadTracker:
    def __init__(self, url, filepath, filename):
        self.url = url
        self.filepath = filepath
        self.filename = filename
        self.progress = 0
        self.total = 0
        self.status = "pending"
        self.error = None


async def download_file(tracker):
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
                        f.write(chunk)
                        tracker.progress += len(chunk)
        os.rename(tmp_path, tracker.filepath)
        tracker.status = "completed"
    except Exception as e:
        tracker.status = "error"
        tracker.error = str(e)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


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
    tracker = DownloadTracker(url, filepath, filename)
    _downloads[key] = tracker

    asyncio.create_task(download_file(tracker))

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


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
