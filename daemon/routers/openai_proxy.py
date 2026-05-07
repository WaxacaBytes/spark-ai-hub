"""OpenAI-compatible proxy.

One stable endpoint (the Hub itself) that forwards every /v1/* request to
whichever LLM is loaded on the upstream slot. POST bodies that carry a
"model" field have it rewritten to the actually-loaded model so clients
can be configured once with any placeholder and survive model swaps in
the Hub.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
import time
from typing import Any

import aiohttp
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daemon.config import settings

router = APIRouter(prefix="/v1", tags=["openai"])

_MODEL_CACHE: dict[str, Any] = {"name": None, "fetched_at": 0.0}
_MODEL_CACHE_TTL = 5.0  # seconds

# Hop-by-hop headers we shouldn't forward in either direction
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


async def _fetch_current_model() -> str | None:
    now = time.time()
    if _MODEL_CACHE["name"] and (now - _MODEL_CACHE["fetched_at"]) < _MODEL_CACHE_TTL:
        return _MODEL_CACHE["name"]
    url = f"{settings.upstream_openai_url.rstrip('/')}/models"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
    except Exception:
        return None
    items = data.get("data") or []
    if not items:
        return None
    name = items[0].get("id")
    if name:
        _MODEL_CACHE["name"] = name
        _MODEL_CACHE["fetched_at"] = now
    return name


def _no_model_running() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "no_model_running",
                "message": "No model is currently running. Launch one from the Spark AI Hub first.",
            }
        },
    )


def _filter_headers(src) -> dict[str, str]:
    return {k: v for k, v in src.items() if k.lower() not in _HOP_BY_HOP}


def _extract_pdf_text_from_data_url(url: str) -> str | None:
    if not url.startswith("data:application/pdf;base64,"):
        return None
    if not shutil.which("pdftotext"):
        return None
    try:
        raw = base64.b64decode(url.split(",", 1)[1], validate=True)
    except (IndexError, ValueError):
        return None
    with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
        f.write(raw)
        f.flush()
        proc = subprocess.run(
            ["pdftotext", "-layout", f.name, "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=30,
        )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _normalize_pdf_file_parts(body: dict) -> bool:
    mutated = False
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for idx, part in enumerate(content):
            if not isinstance(part, dict) or part.get("type") != "file":
                continue
            file_part = part.get("file")
            if not isinstance(file_part, dict):
                continue
            text = _extract_pdf_text_from_data_url(file_part.get("file_data") or "")
            if text is None:
                continue
            filename = file_part.get("filename") or "attachment.pdf"
            content[idx] = {
                "type": "text",
                "text": f"[PDF attachment: {filename}]\n{text}",
            }
            mutated = True
    return mutated


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def proxy(path: str, request: Request):
    upstream_base = settings.upstream_openai_url.rstrip("/")
    url = f"{upstream_base}/{path}"

    raw_body = await request.body()

    # Rewrite model field on JSON POST/PUT/PATCH bodies that carry one,
    # and patch role="developer" → "system" for vLLM compat (Responses API).
    if request.method in ("POST", "PUT", "PATCH") and raw_body:
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None
        if isinstance(body, dict):
            mutated = False
            if "model" in body:
                current = await _fetch_current_model()
                if not current:
                    return _no_model_running()
                body["model"] = current
                mutated = True
            if _normalize_pdf_file_parts(body):
                mutated = True
            # vLLM compat: messages with role="developer" must become "system",
            # and Responses-API "input" items with role="developer" must be
            # folded into top-level "instructions" (vLLM rejects mixed system
            # placements when both fields are present).
            messages = body.get("messages")
            if isinstance(messages, list):
                for item in messages:
                    if isinstance(item, dict) and item.get("role") == "developer":
                        item["role"] = "system"
                        mutated = True
            input_items = body.get("input")
            if isinstance(input_items, list):
                extra_instr_parts: list[str] = []
                kept: list[Any] = []
                for item in input_items:
                    if isinstance(item, dict) and item.get("role") == "developer":
                        content = item.get("content")
                        if isinstance(content, str):
                            extra_instr_parts.append(content)
                        elif isinstance(content, list):
                            for c in content:
                                if isinstance(c, dict):
                                    text = c.get("text") or c.get("content")
                                    if isinstance(text, str):
                                        extra_instr_parts.append(text)
                        mutated = True
                        continue
                    kept.append(item)
                if extra_instr_parts:
                    existing = body.get("instructions") or ""
                    body["instructions"] = (
                        existing + ("\n\n" if existing else "") + "\n\n".join(extra_instr_parts)
                    )
                    body["input"] = kept
            if mutated:
                raw_body = json.dumps(body).encode()

    headers = _filter_headers(request.headers)

    streaming = False
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict) and parsed.get("stream"):
                streaming = True
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    params = dict(request.query_params)

    if streaming:
        async def gen():
            timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.request(
                    request.method, url, data=raw_body, headers=headers, params=params
                ) as r:
                    async for chunk in r.content.iter_any():
                        if chunk:
                            yield chunk

        return StreamingResponse(gen(), media_type="text/event-stream")

    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.request(
            request.method, url, data=raw_body, headers=headers, params=params
        ) as r:
            content = await r.read()
            resp_headers = _filter_headers(r.headers)
            return Response(
                content=content,
                status_code=r.status,
                headers=resp_headers,
                media_type=r.headers.get("content-type"),
            )
