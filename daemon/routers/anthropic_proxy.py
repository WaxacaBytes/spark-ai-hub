"""Anthropic Messages API compatibility.

Mirrors Ollama's pattern (since v0.14.0): expose POST /v1/messages on the Hub,
translate the Anthropic Messages shape into upstream OpenAI Chat Completions,
and translate the response back. Lets Claude Code (and any Anthropic-shaped
client) target the Hub directly via ANTHROPIC_BASE_URL.

Same upstream as openai_proxy.py, same model-rewrite trick: the client's
"model" field is replaced with whatever's loaded on the Hub slot.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from daemon.config import settings
from daemon.routers.openai_proxy import _fetch_current_model, _no_model_running

router = APIRouter(tags=["anthropic"])


_FINISH_TO_STOP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
    None: "end_turn",
}


def _flatten_text(value: Any) -> str:
    """Anthropic system/content fields are either str or [{type:'text',text}, ...]."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(
            b.get("text") or ""
            for b in value
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _anthropic_to_openai(body: dict, model: str) -> dict:
    out: dict[str, Any] = {"model": model, "stream": bool(body.get("stream"))}

    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]
    for k in ("temperature", "top_p"):
        if k in body:
            out[k] = body[k]
    if stops := body.get("stop_sequences"):
        out["stop"] = stops
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}

    messages: list[dict] = []
    if sys_text := _flatten_text(body.get("system")):
        messages.append({"role": "system", "content": sys_text})

    for msg in body.get("messages", []):
        role = msg.get("role")
        content = msg.get("content")

        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            continue

        if role == "user":
            user_parts: list[dict] = []
            user_has_image = False
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                t = blk.get("type")
                if t == "tool_result":
                    if user_parts:
                        messages.append({"role": "user", "content": _user_content(user_parts, user_has_image)})
                        user_parts, user_has_image = [], False
                    tc_content = blk.get("content")
                    if isinstance(tc_content, list):
                        tc_content = _flatten_text(tc_content)
                    elif not isinstance(tc_content, str):
                        tc_content = json.dumps(tc_content) if tc_content is not None else ""
                    messages.append({
                        "role": "tool",
                        "tool_call_id": blk.get("tool_use_id", ""),
                        "content": tc_content or "",
                    })
                elif t == "text":
                    user_parts.append({"type": "text", "text": blk.get("text") or ""})
                elif t == "image":
                    src = blk.get("source") or {}
                    if src.get("type") == "base64":
                        url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                    else:
                        url = src.get("url", "")
                    user_parts.append({"type": "image_url", "image_url": {"url": url}})
                    user_has_image = True
            if user_parts:
                messages.append({"role": "user", "content": _user_content(user_parts, user_has_image)})

        elif role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                t = blk.get("type")
                if t == "text":
                    text_parts.append(blk.get("text") or "")
                elif t == "tool_use":
                    tool_calls.append({
                        "id": blk.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": blk.get("name", ""),
                            "arguments": json.dumps(blk.get("input") or {}),
                        },
                    })
            asst: dict[str, Any] = {"role": "assistant"}
            text = "".join(text_parts)
            asst["content"] = text if text else None
            if tool_calls:
                asst["tool_calls"] = tool_calls
            messages.append(asst)

    out["messages"] = messages

    if tools := body.get("tools"):
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                },
            }
            for t in tools if isinstance(t, dict)
        ]

    if tc := body.get("tool_choice"):
        ttype = tc.get("type")
        if ttype == "auto":
            out["tool_choice"] = "auto"
        elif ttype == "any":
            out["tool_choice"] = "required"
        elif ttype == "tool" and tc.get("name"):
            out["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
        elif ttype == "none":
            out["tool_choice"] = "none"

    return out


def _user_content(parts: list[dict], has_image: bool) -> Any:
    if not has_image:
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return parts


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _openai_to_anthropic(resp: dict, requested_model: str) -> dict:
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    blocks: list[dict] = []
    if text := msg.get("content"):
        blocks.append({"type": "text", "text": text})
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id") or _new_id("toolu"),
            "name": fn.get("name") or "",
            "input": args,
        })
    usage = resp.get("usage") or {}
    return {
        "id": resp.get("id") or _new_id("msg"),
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": blocks,
        "stop_reason": _FINISH_TO_STOP.get(choice.get("finish_reason"), "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


async def _stream_translate(
    upstream: aiohttp.ClientResponse, requested_model: str
) -> AsyncIterator[bytes]:
    msg_id = _new_id("msg")
    yield _sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": requested_model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })

    text_idx: int | None = None
    tool_blocks: dict[int, dict] = {}
    next_idx = 0
    finish_reason: str | None = None
    output_tokens = 0

    buffer = b""
    async for chunk in upstream.content.iter_any():
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line or not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                continue
            try:
                ev = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if usage := ev.get("usage"):
                output_tokens = usage.get("completion_tokens", output_tokens)

            choices = ev.get("choices") or []
            if not choices:
                continue
            ch = choices[0]
            delta = ch.get("delta") or {}
            if fr := ch.get("finish_reason"):
                finish_reason = fr

            if text := delta.get("content"):
                if text_idx is None:
                    text_idx = next_idx
                    next_idx += 1
                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": text_idx,
                        "content_block": {"type": "text", "text": ""},
                    })
                yield _sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": text_idx,
                    "delta": {"type": "text_delta", "text": text},
                })

            for tc in delta.get("tool_calls") or []:
                tci = tc.get("index", 0)
                fn = tc.get("function") or {}
                if tci not in tool_blocks:
                    a_idx = next_idx
                    next_idx += 1
                    tool_blocks[tci] = {
                        "anthropic_idx": a_idx,
                        "id": tc.get("id") or _new_id("toolu"),
                        "name": fn.get("name") or "",
                    }
                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": a_idx,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_blocks[tci]["id"],
                            "name": tool_blocks[tci]["name"],
                            "input": {},
                        },
                    })
                if args := fn.get("arguments"):
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": tool_blocks[tci]["anthropic_idx"],
                        "delta": {"type": "input_json_delta", "partial_json": args},
                    })

    if text_idx is not None:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": text_idx})
    for b in tool_blocks.values():
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": b["anthropic_idx"]})

    yield _sse("message_delta", {
        "type": "message_delta",
        "delta": {
            "stop_reason": _FINISH_TO_STOP.get(finish_reason, "end_turn"),
            "stop_sequence": None,
        },
        "usage": {"output_tokens": output_tokens},
    })
    yield _sse("message_stop", {"type": "message_stop"})


@router.post("/v1/messages")
async def messages(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(status_code=400, content={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "invalid JSON body"},
        })

    requested_model = body.get("model") or "claude"
    current = await _fetch_current_model()
    if not current:
        return _no_model_running()

    upstream_payload = _anthropic_to_openai(body, current)
    url = f"{settings.upstream_openai_url.rstrip('/')}/chat/completions"

    if upstream_payload.get("stream"):
        async def gen():
            timeout = aiohttp.ClientTimeout(total=None, sock_read=None)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                async with s.post(url, json=upstream_payload) as r:
                    if r.status != 200:
                        err = (await r.text())[:500]
                        yield _sse("error", {
                            "type": "error",
                            "error": {"type": "api_error", "message": err},
                        })
                        return
                    async for ev in _stream_translate(r, requested_model):
                        yield ev
        return StreamingResponse(gen(), media_type="text/event-stream")

    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.post(url, json=upstream_payload) as r:
            try:
                data = await r.json()
            except aiohttp.ContentTypeError:
                data = {"error": {"message": (await r.text())[:500]}}
            if r.status != 200:
                err_msg = data.get("error", {}).get("message") if isinstance(data, dict) else str(data)
                return JSONResponse(status_code=r.status, content={
                    "type": "error",
                    "error": {"type": "api_error", "message": err_msg or "upstream error"},
                })
    return JSONResponse(_openai_to_anthropic(data, requested_model))


@router.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    """Rough char-based estimate. Claude Code calls this (often with ?beta=true)
    before each turn — returning a sane number instead of 404/hang is what
    avoids the failure modes in anthropics/claude-code#51239 and ollama#13949.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    blob = json.dumps({
        "system": body.get("system"),
        "messages": body.get("messages"),
        "tools": body.get("tools"),
    })
    return JSONResponse({"input_tokens": max(1, len(blob) // 4)})
