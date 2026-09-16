"""OAuth 2.1 authorization server for the Hub's MCP endpoint.

Claude's hosted connectors (claude.ai, Claude Desktop, mobile) cannot send a
Hub API key: a custom connector is a URL plus OAuth. So the Hub is its own
authorization server, and the account that signs in on the consent page is the
account the connector acts as — the same users, the same approval rules.

What Claude needs, and what this implements:

* RFC 9728 protected-resource metadata and RFC 8414 server metadata, so the
  client can find the endpoints from a 401 on /mcp.
* Client registration both ways Claude supports: Dynamic Client Registration
  (RFC 7591) and Client ID Metadata Documents (the client_id is an https URL
  whose JSON lists the client's redirect URIs). Every client is public — PKCE
  S256 is mandatory and there are no client secrets.
* Authorization code + refresh token grants. Refresh tokens rotate on use.

Tokens are scoped to /mcp only; they never open /v1 or /api. Like sessions,
only SHA-256 digests of codes and tokens are stored.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import secrets
import socket
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import aiohttp

from daemon.db import get_db
from daemon.services.auth_service import now_iso, token_digest

SCOPE = "mcp"
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 24 * 3600
CODE_TTL = 600
MAX_CLIENTS = 5000
CIMD_TTL = 600
CIMD_MAX_BYTES = 64 * 1024
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

_CACHE_TTL = 15.0
_access_cache: dict[str, tuple[float, dict | None]] = {}
_cimd_cache: dict[str, tuple[float, dict | None]] = {}


class OAuthError(Exception):
    """An RFC 6749 error: `error` is the code a client acts on."""

    def __init__(self, error: str, description: str = "", status: int = 400):
        super().__init__(description or error)
        self.error = error
        self.description = description
        self.status = status

    def body(self) -> dict:
        out = {"error": self.error}
        if self.description:
            out["error_description"] = self.description
        return out


def _expires(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")


def invalidate_caches() -> None:
    _access_cache.clear()


# ── redirect URIs ───────────────────────────────────────────────────────────


def valid_redirect_uri(uri: str) -> bool:
    """https anywhere, or plain http on a loopback address (native clients)."""
    try:
        parts = urllib.parse.urlsplit(uri)
    except ValueError:
        return False
    if parts.fragment or not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and parts.hostname.lower() in LOOPBACK_HOSTS


def redirect_matches(registered: list[str], given: str) -> bool:
    """Exact match, except a loopback redirect may use any port (RFC 8252 §7.3)."""
    if given in registered:
        return True
    try:
        g = urllib.parse.urlsplit(given)
    except ValueError:
        return False
    if g.scheme != "http" or (g.hostname or "").lower() not in LOOPBACK_HOSTS:
        return False
    for uri in registered:
        r = urllib.parse.urlsplit(uri)
        if (r.scheme == "http" and (r.hostname or "").lower() == (g.hostname or "").lower()
                and r.path == g.path and r.query == g.query):
            return True
    return False


# ── clients ─────────────────────────────────────────────────────────────────


async def register_client(meta: dict) -> dict:
    """RFC 7591 dynamic registration of a public client."""
    uris = meta.get("redirect_uris")
    if not isinstance(uris, list) or not uris or not all(isinstance(u, str) for u in uris):
        raise OAuthError("invalid_redirect_uri", "redirect_uris must be a non-empty list of URLs.")
    for uri in uris:
        if not valid_redirect_uri(uri):
            raise OAuthError("invalid_redirect_uri",
                             f"{uri} must be https, or http on a loopback address.")
    method = meta.get("token_endpoint_auth_method") or "none"
    if method != "none":
        raise OAuthError("invalid_client_metadata",
                         "Only public clients are supported (token_endpoint_auth_method=none).")
    name = str(meta.get("client_name") or "")[:100]

    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) AS n FROM oauth_clients") as cur:
            if (await cur.fetchone())["n"] >= MAX_CLIENTS:
                raise OAuthError("invalid_client_metadata",
                                 "This Hub has too many registered clients.", status=503)
        client_id = "sah-client-" + secrets.token_urlsafe(18)
        await db.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris) VALUES (?, ?, ?)",
            (client_id, name, json.dumps(uris)),
        )
        await db.commit()
    finally:
        await db.close()
    return {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "client_name": name,
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


def _host_is_public(host: str, port: int) -> bool:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError:
        return False
    return bool(infos) and all(ipaddress.ip_address(i[4][0]).is_global for i in infos)


async def _fetch_metadata_document(client_id: str) -> dict | None:
    """A Client ID Metadata Document: the client_id URL serves the client's JSON."""
    hit = _cimd_cache.get(client_id)
    if hit and time.time() - hit[0] < CIMD_TTL:
        return hit[1]
    client = None
    try:
        parts = urllib.parse.urlsplit(client_id)
        # The daemon fetches whatever URL a stranger names, so it must not be
        # a way to reach anything that is not on the public internet.
        if parts.scheme == "https" and parts.hostname and await asyncio.to_thread(
                _host_is_public, parts.hostname, parts.port or 443):
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(client_id, allow_redirects=False) as r:
                    raw = await r.content.read(CIMD_MAX_BYTES + 1)
                    ok = r.status == 200 and len(raw) <= CIMD_MAX_BYTES
            doc = json.loads(raw) if ok else None
            uris = doc.get("redirect_uris") if isinstance(doc, dict) else None
            if (isinstance(doc, dict) and doc.get("client_id") == client_id
                    and isinstance(uris, list) and uris
                    and all(isinstance(u, str) and valid_redirect_uri(u) for u in uris)):
                client = {
                    "client_id": client_id,
                    "client_name": str(doc.get("client_name") or parts.hostname)[:100],
                    "redirect_uris": uris,
                }
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeDecodeError):
        client = None
    _cimd_cache[client_id] = (time.time(), client)
    return client


async def get_client(client_id: str) -> dict | None:
    if not client_id:
        return None
    if client_id.startswith("https://"):
        return await _fetch_metadata_document(client_id)
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    if not row:
        return None
    return {"client_id": row["client_id"], "client_name": row["client_name"],
            "redirect_uris": json.loads(row["redirect_uris"])}


# ── codes and tokens ────────────────────────────────────────────────────────


def pkce_matches(verifier: str, challenge: str) -> bool:
    if not verifier or not 43 <= len(verifier) <= 128:
        return False
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(digest, challenge)


async def create_code(*, client_id: str, user_id: int, redirect_uri: str,
                      code_challenge: str, resource: str, scope: str) -> str:
    code = secrets.token_urlsafe(32)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO oauth_codes (code_hash, client_id, user_id, redirect_uri,
                                        code_challenge, resource, scope, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_digest(code), client_id, user_id, redirect_uri, code_challenge,
             resource, scope, _expires(CODE_TTL)),
        )
        await db.commit()
    finally:
        await db.close()
    return code


async def _issue(db, client_id: str, user_id: int, resource: str, scope: str) -> dict:
    access = "sah-oat-" + secrets.token_urlsafe(32)
    refresh = "sah-ort-" + secrets.token_urlsafe(32)
    await db.executemany(
        """INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, resource, scope, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (token_digest(access), "access", client_id, user_id, resource, scope, _expires(ACCESS_TTL)),
            (token_digest(refresh), "refresh", client_id, user_id, resource, scope, _expires(REFRESH_TTL)),
        ],
    )
    await db.commit()
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL,
        "refresh_token": refresh,
        "scope": scope,
    }


async def _active_user(db, user_id: int) -> bool:
    async with db.execute("SELECT status FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return bool(row) and row["status"] == "active"


async def exchange_code(*, code: str, client_id: str, redirect_uri: str,
                        code_verifier: str, resource: str | None) -> dict:
    db = await get_db()
    try:
        digest = token_digest(code or "")
        async with db.execute("SELECT * FROM oauth_codes WHERE code_hash = ?", (digest,)) as cur:
            row = await cur.fetchone()
        # Deleted before any check: a code is single-use even when the attempt fails.
        cur = await db.execute("DELETE FROM oauth_codes WHERE code_hash = ?", (digest,))
        await db.commit()
        if not row or cur.rowcount == 0:
            raise OAuthError("invalid_grant", "The authorization code is invalid or was already used.")
        if row["expires_at"] < now_iso():
            raise OAuthError("invalid_grant", "The authorization code has expired.")
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            raise OAuthError("invalid_grant", "The code was issued to a different client or redirect_uri.")
        if not pkce_matches(code_verifier, row["code_challenge"]):
            raise OAuthError("invalid_grant", "PKCE verification failed.")
        if resource and resource.rstrip("/") != row["resource"]:
            raise OAuthError("invalid_target", "resource does not match the authorization request.")
        if not await _active_user(db, row["user_id"]):
            raise OAuthError("invalid_grant", "The account is no longer active.")
        return await _issue(db, client_id, row["user_id"], row["resource"], row["scope"])
    finally:
        await db.close()


async def refresh(*, refresh_token: str, client_id: str, resource: str | None) -> dict:
    db = await get_db()
    try:
        digest = token_digest(refresh_token or "")
        async with db.execute(
            "SELECT * FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (digest,)
        ) as cur:
            row = await cur.fetchone()
        # Rotation: the presented refresh token dies here whatever happens next.
        cur = await db.execute("DELETE FROM oauth_tokens WHERE token_hash = ? AND kind = 'refresh'", (digest,))
        await db.commit()
        if not row or cur.rowcount == 0 or row["expires_at"] < now_iso():
            raise OAuthError("invalid_grant", "The refresh token is invalid, expired or already used.")
        if row["client_id"] != client_id:
            raise OAuthError("invalid_grant", "The refresh token was issued to a different client.")
        if resource and resource.rstrip("/") != row["resource"]:
            raise OAuthError("invalid_target", "resource does not match the original grant.")
        if not await _active_user(db, row["user_id"]):
            raise OAuthError("invalid_grant", "The account is no longer active.")
        return await _issue(db, client_id, row["user_id"], row["resource"], row["scope"])
    finally:
        await db.close()


async def user_for_access_token(token: str) -> dict | None:
    """Resolve an OAuth bearer token to its *active* account, or None."""
    if not token:
        return None
    digest = token_digest(token)
    hit = _access_cache.get(digest)
    now = time.time()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]
    db = await get_db()
    try:
        async with db.execute(
            """SELECT u.* FROM oauth_tokens t JOIN users u ON u.id = t.user_id
               WHERE t.token_hash = ? AND t.kind = 'access' AND t.expires_at > ?
                 AND u.status = 'active'""",
            (digest, now_iso()),
        ) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    user = dict(row) if row else None
    _access_cache[digest] = (now, user)
    return user


async def revoke_user_tokens(user_id: int) -> int:
    db = await get_db()
    try:
        cur = await db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        await db.commit()
        count = cur.rowcount
    finally:
        await db.close()
    invalidate_caches()
    return count


async def purge_expired() -> None:
    db = await get_db()
    try:
        now = now_iso()
        await db.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (now,))
        await db.execute("DELETE FROM oauth_tokens WHERE expires_at < ?", (now,))
        await db.commit()
    finally:
        await db.close()
