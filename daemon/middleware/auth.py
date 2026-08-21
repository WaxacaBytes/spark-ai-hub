"""Gate every API, WebSocket and model request on a real account.

Implemented as raw ASGI rather than `BaseHTTPMiddleware` because that class
only ever sees `http` scopes — the Hub's build-log and metrics WebSockets would
have stayed wide open behind it, which on an internet-exposed Hub means anyone
can stream a container's logs without logging in.

What is public, and why:

* The static SPA bundle and everything else that is not `/api`, `/ws` or `/v1`.
  It has to load before it can render the sign-in screen, and it carries no
  data of its own — every byte the user actually cares about is behind `/api`.
* The `sah` CLI download. It is the same code published on GitHub, and a new
  device must be able to fetch it before it has anything to authenticate with.
* The handful of `/api/auth/*` routes you need in order to sign in at all.
"""
from __future__ import annotations

import json

from starlette.types import ASGIApp, Receive, Scope, Send

from daemon.config import settings
from daemon.services import auth_service

# Routes reachable with no credentials. Exact matches only — no prefixes, so a
# future /api/auth/admin-ish route cannot be let in by accident.
PUBLIC_API_PATHS = {
    "/api/auth/state",
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/logout",
}

GUARDED_PREFIXES = ("/api", "/ws", "/v1")


def _needs_auth(path: str) -> bool:
    if path in PUBLIC_API_PATHS:
        return False
    return path.startswith(GUARDED_PREFIXES)


def _bearer(headers: dict[bytes, bytes]) -> str | None:
    """Pull an API key out of whichever header the client's SDK uses.

    OpenAI clients send `Authorization: Bearer`, Anthropic clients send
    `x-api-key`, and a few send both — accept all of them.
    """
    auth = headers.get(b"authorization", b"").decode(errors="ignore")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return token
    for name in (b"x-api-key", b"api-key"):
        val = headers.get(name, b"").decode(errors="ignore").strip()
        if val:
            return val
    return None


def _cookie(headers: dict[bytes, bytes], name: str) -> str | None:
    raw = headers.get(b"cookie", b"").decode(errors="ignore")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


async def resolve_user(scope: Scope) -> tuple[dict | None, str]:
    """Identify the caller. Returns (user, source); user is None if anonymous."""
    headers = {k.lower(): v for k, v in scope.get("headers", [])}

    token = _cookie(headers, settings.session_cookie_name)
    if token:
        user = await auth_service.user_for_session(token)
        if user:
            return user, "session"

    key = _bearer(headers)
    if key:
        user = await auth_service.user_for_api_key(key)
        if user:
            return user, "api_key"

    return None, ""


class AuthMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not settings.auth_enabled:
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        user, source = await resolve_user(scope)

        # Downstream handlers read these off `request.state`.
        state = scope.setdefault("state", {})
        state["user"] = user
        state["auth_source"] = source

        if user is None and _needs_auth(path):
            await self._deny(scope, receive, send, path)
            return

        await self.app(scope, receive, send)

    async def _deny(self, scope: Scope, receive: Receive, send: Send, path: str) -> None:
        if scope["type"] == "websocket":
            # A WebSocket is refused by closing the handshake, never by
            # accepting first — accepting would hand out a live socket.
            await receive()
            await send({"type": "websocket.close", "code": 1008})
            return

        if path.startswith("/v1"):
            # OpenAI/Anthropic SDKs surface `error.message` to the user, so put
            # the actual instructions there rather than a bare "Unauthorized".
            body = {
                "error": {
                    "type": "authentication_error",
                    "code": "invalid_api_key",
                    "message": (
                        "Missing or invalid API key. Sign in to Spark AI Hub and "
                        "copy your key from Account, or run the sah installer again."
                    ),
                }
            }
        else:
            body = {"detail": "Authentication required."}

        payload = json.dumps(body).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                # Tells an SDK it is an auth failure, not a broken endpoint.
                (b"www-authenticate", b'Bearer realm="Spark AI Hub"'),
            ],
        })
        await send({"type": "http.response.body", "body": payload})
