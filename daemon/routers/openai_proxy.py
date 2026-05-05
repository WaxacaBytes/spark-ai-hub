"""OpenAI-compatible proxy.

One stable endpoint (the Hub itself) that forwards to whatever LLM is
currently running on the upstream slot. The model name in incoming requests
is rewritten to whatever the upstream actually has loaded — clients can be
configured once with any placeholder and never touched again as the user
swaps models in the Hub.
"""
from __future__ import annotations

import time
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daemon.config import settings

router = APIRouter(prefix="/v1", tags=["openai"])

_MODEL_CACHE: dict[str, Any] = {"name": None, "fetched_at": 0.0}
_MODEL_CACHE_TTL = 5.0  # seconds


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


@router.get("/models")
async def list_models() -> JSONResponse:
    url = f"{settings.upstream_openai_url.rstrip('/')}/models"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return _no_model_running()
                data = await r.json()
    except Exception:
        return _no_model_running()
    return JSONResponse(content=data)


async def _proxy_post(request: Request, path: str) -> Any:
    current = await _fetch_current_model()
    if not current:
        return _no_model_running()

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be valid JSON")

    body["model"] = current
    streaming = bool(body.get("stream"))
    url = f"{settings.upstream_openai_url.rstrip('/')}/{path}"

    headers = {"content-type": "application/json"}
    if auth := request.headers.get("authorization"):
        headers["authorization"] = auth

    if streaming:

        async def gen():
            timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, json=body, headers=headers) as r:
                    async for chunk in r.content.iter_any():
                        if chunk:
                            yield chunk

        return StreamingResponse(gen(), media_type="text/event-stream")

    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=body, headers=headers) as r:
            content = await r.read()
            return Response(
                content=content,
                status_code=r.status,
                media_type=r.headers.get("content-type", "application/json"),
            )


@router.post("/chat/completions")
async def chat_completions(request: Request):
    return await _proxy_post(request, "chat/completions")


@router.post("/completions")
async def completions(request: Request):
    return await _proxy_post(request, "completions")


@router.post("/embeddings")
async def embeddings(request: Request):
    return await _proxy_post(request, "embeddings")


@router.post("/responses")
async def responses(request: Request):
    return await _proxy_post(request, "responses")
