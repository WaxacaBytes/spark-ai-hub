"""OpenAI-compatible proxy.

One stable endpoint (the Hub itself) in front of every LLM that is running.
Each model server publishes no host port: it sits on the Hub's app network and
is reached through the front door at /run/{slug}/v1. A request goes to the
server that serves the model it names; one naming anything else goes to the
largest model up, with its "model" field rewritten, so clients configured
once with any placeholder survive model swaps in the Hub.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from daemon.services import auth_service, proxy_service
from daemon.services.registry_service import get_recipes

router = APIRouter(prefix="/v1", tags=["openai"])

# Hop-by-hop headers we shouldn't forward in either direction
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

# The client authenticates to the *Hub*, not to the model server behind it.
# Forwarding the Hub API key onward would hand a user's credential to every
# container a recipe happens to run, so it stops here.
_CREDENTIAL_HEADERS = {"authorization", "x-api-key", "api-key", "cookie"}


# Upstreams ------------------------------------------------------------------
#
# OpenAI's /v1/models object carries no capability information, and neither
# vLLM nor SGLang add any — they only extend it with max_model_len. Clients that
# need to know whether a model can see images or call tools are therefore left
# guessing from the model name, which is unreliable and goes stale with every
# new model family.
#
# The Hub already knows the answer: every recipe is hand-tagged. So each
# /v1/models entry is annotated with the capabilities of the recipe serving it
# (the tag → capability mapping lives on the Recipe model, so the detail page
# shows the user exactly what is reported here), plus its size and slug. This
# mirrors how Ollama reports capabilities from /api/show, in the one place an
# OpenAI-compatible client can reach. Unknown fields are ignored by
# OpenAI-shaped clients, so this is additive for anything that does not look.

@dataclass
class Upstream:
    slug: str
    base: str            # {front door}/run/{slug}/v1
    model: str           # the id the server itself answers to
    entries: list[dict]  # its annotated /v1/models entries, as the Hub lists them


_UPSTREAM_CACHE: dict[str, Any] = {"list": [], "fetched_at": 0.0}
_UPSTREAM_CACHE_TTL = 5.0  # seconds


async def _running_slugs() -> set[str]:
    """Slugs of every running Hub compose project, from one docker call."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "ps", "--format", '{{.Label "com.docker.compose.project"}}',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
    except Exception:
        return set()
    prefix = "spark-ai-hub-"
    return {
        line[len(prefix):]
        for line in stdout.decode().split()
        if line.startswith(prefix)
    }


async def _upstream(session: aiohttp.ClientSession, recipe) -> Upstream | None:
    """One running model server, or None while it is still loading."""
    base = f"{proxy_service.internal_url(recipe.slug)}/v1"
    try:
        async with session.get(f"{base}/models", headers=proxy_service.probe_headers()) as r:
            if r.status != 200:
                return None
            data = await r.json(content_type=None)
    except Exception:
        return None
    entries = [e for e in (data or {}).get("data") or [] if isinstance(e, dict) and e.get("id")]
    for entry in entries:
        entry.setdefault("capabilities", recipe.capabilities)
        entry["params_b"] = recipe.params_b
        entry["recipe"] = recipe.slug
    return Upstream(recipe.slug, base, entries[0]["id"], entries) if entries else None


async def _upstreams() -> list[Upstream]:
    """Every model server that answers, largest first."""
    now = time.time()
    if _UPSTREAM_CACHE["list"] and (now - _UPSTREAM_CACHE["fetched_at"]) < _UPSTREAM_CACHE_TTL:
        return _UPSTREAM_CACHE["list"]
    recipes = get_recipes()
    running = [recipes[s] for s in await _running_slugs() if s in recipes and recipes[s].is_llm]
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
        found = await asyncio.gather(*(_upstream(session, r) for r in running))
    size = {r.slug: (r.params_b or 0, r.memory_gb) for r in running}
    ups = sorted((u for u in found if u), key=lambda u: size[u.slug], reverse=True)
    # Two builds of one model (with and without a drafter, say) answer to the
    # same id. Listing it twice would leave all but one unreachable by name,
    # so each copy is listed under its recipe slug instead.
    counts = collections.Counter(e["id"] for u in ups for e in u.entries)
    for up in ups:
        for entry in up.entries:
            if counts[entry["id"]] > 1:
                entry["id"] = up.slug
    if ups:
        _UPSTREAM_CACHE["list"] = ups
        _UPSTREAM_CACHE["fetched_at"] = now
    return ups


async def route(requested: Any) -> tuple[Upstream, str] | None:
    """The server for the model a request names, and the id to send it.

    A model is named by its listed id, its served id or its recipe slug; a
    served id shared by several goes to the largest of them. Any other name
    goes to the largest model up. None when nothing is running.
    """
    ups = await _upstreams()
    if not ups:
        return None
    for up in ups:
        if requested in (up.slug, up.model) or any(e["id"] == requested for e in up.entries):
            return up, up.model
    return ups[0], ups[0].model


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


def _upstream_headers(src) -> dict[str, str]:
    drop = _HOP_BY_HOP | _CREDENTIAL_HEADERS
    return {k: v for k, v in src.items() if k.lower() not in drop}


# ── usage accounting ────────────────────────────────────────────────────────

def _usage_from(payload: Any) -> dict | None:
    """Pull an OpenAI-shaped usage object out of a response body.

    The Responses API names the same two numbers differently from Chat
    Completions, so both spellings are read here.
    """
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        # Responses-API stream events wrap the whole response one level down
        # (`response.completed` → {"response": {..., "usage": {...}}}).
        inner = payload.get("response")
        if isinstance(inner, dict):
            return _usage_from(inner)
        return None
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    total = usage.get("total_tokens", 0) or (prompt + completion)
    return {
        "prompt_tokens": int(prompt),
        "completion_tokens": int(completion),
        "total_tokens": int(total),
        "model": payload.get("model") or "",
    }


def _inspect_sse_line(line: bytes, client_wants_usage: bool) -> tuple[dict | None, bool]:
    """Read one SSE line. Returns (usage-if-this-was-the-usage-event, drop-it).

    Only a *Chat Completions* usage-only chunk is ours to swallow: it is the
    one we asked for on the client's behalf by injecting stream_options. The
    check is deliberately narrow, because the Responses API's terminal
    `response.completed` event also carries usage and also has no `choices` —
    dropping that one ends the stream before the client's terminal event and
    breaks every Responses-API client (Codex, ChatGPT, Muse).
    """
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return None, False
    payload = stripped[5:].strip()
    if not payload or payload == b"[DONE]":
        return None, False
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, False
    usage = _usage_from(obj)
    if not usage:
        return None, False
    usage_only_chunk = (
        obj.get("object") == "chat.completion.chunk"
        and isinstance(obj.get("usage"), dict)
        and not obj.get("choices")
    )
    return usage, (usage_only_chunk and not client_wants_usage)


async def record_call(request: Request, endpoint: str, usage: dict | None) -> None:
    """Attribute one completed model call to whoever made it.

    Anonymous calls are only possible with auth disabled, and there is nobody
    to attribute those to, so they are simply not logged.
    """
    user = getattr(request.state, "user", None)
    if not user:
        return
    usage = usage or {}
    await auth_service.record_usage(
        user["id"],
        model=usage.get("model", ""),
        endpoint=endpoint,
        prompt_tokens=usage.get("prompt_tokens", 0),
        completion_tokens=usage.get("completion_tokens", 0),
        total_tokens=usage.get("total_tokens", 0),
        source=getattr(request.state, "auth_source", "") or "",
    )


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
    # The model list is the Hub's own answer, merged from every running server.
    if request.method == "GET" and path.strip("/") == "models":
        entries = [e for up in await _upstreams() for e in up.entries]
        return JSONResponse({"object": "list", "data": entries})

    raw_body = await request.body()
    body: Any = None
    if request.method in ("POST", "PUT", "PATCH") and raw_body:
        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = None

    routed = await route(body.get("model") if isinstance(body, dict) else None)
    if not routed:
        return _no_model_running()
    upstream, model_id = routed
    url = f"{upstream.base}/{path}"

    # Rewrite model field on JSON POST/PUT/PATCH bodies that carry one,
    # and patch role="developer" → "system" for vLLM compat (Responses API).
    if isinstance(body, dict):
        mutated = False
        if "model" in body and body["model"] != model_id:
            body["model"] = model_id
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

    upstream_headers = {**_upstream_headers(request.headers), **proxy_service.probe_headers()}

    streaming = False
    client_wants_usage = False
    if raw_body:
        try:
            parsed = json.loads(raw_body)
            if isinstance(parsed, dict) and parsed.get("stream"):
                streaming = True
                opts = parsed.get("stream_options")
                client_wants_usage = bool(
                    isinstance(opts, dict) and opts.get("include_usage")
                )
                # A streamed response carries no usage block unless it is asked
                # for, which would leave every streaming call — i.e. nearly all
                # of them — unaccounted for. So we always ask, and drop the
                # extra usage-only chunk again on the way out if the client
                # did not want it. The client sees byte-identical output.
                if not client_wants_usage:
                    parsed["stream_options"] = {
                        **(opts if isinstance(opts, dict) else {}),
                        "include_usage": True,
                    }
                    raw_body = json.dumps(parsed).encode()
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

    params = dict(request.query_params)
    endpoint = f"openai:{path.strip('/')}"

    if streaming:
        async def gen():
            captured: dict | None = None
            timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.request(
                    request.method, url, data=raw_body,
                    headers=upstream_headers, params=params,
                ) as r:
                    # Line-buffered so a usage chunk split across two TCP reads
                    # is still recognised as one SSE event.
                    buf = b""
                    async for chunk in r.content.iter_any():
                        if not chunk:
                            continue
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            usage, drop = _inspect_sse_line(line, client_wants_usage)
                            if usage:
                                captured = usage
                            if not drop:
                                yield line + b"\n"
                    if buf:
                        yield buf
            await record_call(request, endpoint, captured)

        return StreamingResponse(gen(), media_type="text/event-stream")

    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.request(
            request.method, url, data=raw_body,
            headers=upstream_headers, params=params,
        ) as r:
            content = await r.read()
            if r.status == 200 and request.method == "POST":
                try:
                    await record_call(
                        request, endpoint, _usage_from(json.loads(content))
                    )
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            resp_headers = _filter_headers(r.headers)
            return Response(
                content=content,
                status_code=r.status,
                headers=resp_headers,
                media_type=r.headers.get("content-type"),
            )
