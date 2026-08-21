"""Accounts, sessions, API keys and usage accounting.

Everything here is stdlib — PBKDF2-HMAC-SHA256 for passwords, `secrets` for
tokens — so exposing the Hub to the internet costs no new dependency.

Two credentials exist, for two audiences:

* **Session cookie** — the browser. The cookie carries a random token; only
  its SHA-256 lands in the database, so the DB alone cannot be replayed.
* **API key** — everything that is not a browser (the `sah` CLI, coding
  agents, any OpenAI/Anthropic client). One key per account, shared across
  every model the Hub serves, because from the client's side the Hub *is* the
  model endpoint and the model behind it changes as apps are launched.

The API key is stored in the clear, unlike the password and the session. That
is deliberate: a user must be able to reopen their account page next month and
copy the same key into a new client. A one-way hash would make the key
show-once, and the realistic threat here (someone reading a file on a machine
they already have shell access to) is not the one hashing defends against.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from daemon.config import settings
from daemon.db import get_db

# ── password hashing ────────────────────────────────────────────────────────

_PBKDF2_ITERATIONS = 260_000
_HASH_PREFIX = "pbkdf2_sha256"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS
    )
    return f"{_HASH_PREFIX}${_PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$", 3)
    except ValueError:
        return False
    if algo != _HASH_PREFIX:
        return False
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), salt.encode(), int(iterations)
    )
    return hmac.compare_digest(dk.hex(), digest)


def validate_email(email: str) -> str:
    email = (email or "").strip()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address.")
    return email


def validate_password(password: str) -> str:
    if len(password or "") < MIN_PASSWORD_LEN:
        raise ValueError(
            f"Password must be at least {MIN_PASSWORD_LEN} characters."
        )
    return password


# ── tokens ──────────────────────────────────────────────────────────────────

API_KEY_PREFIX = "sah-"


def new_api_key() -> str:
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ── credential caches ───────────────────────────────────────────────────────
#
# Every request through the /v1 proxy would otherwise open a SQLite connection
# just to resolve who is calling. These caches keep that off the hot path; the
# TTL is short enough that an admin's approve/reject takes effect promptly.

_CACHE_TTL = 15.0
_session_cache: dict[str, tuple[float, dict | None]] = {}
_apikey_cache: dict[str, tuple[float, dict | None]] = {}


def invalidate_caches() -> None:
    """Drop every cached credential.

    Called whenever an account changes — approval, rejection, password change,
    key rotation, deletion — so a revoked credential stops working now rather
    than up to `_CACHE_TTL` seconds from now.
    """
    _session_cache.clear()
    _apikey_cache.clear()


# ── user helpers ────────────────────────────────────────────────────────────

PUBLIC_FIELDS = (
    "id", "email", "name", "role", "status", "created_at",
    "reviewed_at", "last_login_at",
)


def public_user(row: Any) -> dict:
    d = dict(row)
    return {k: d.get(k) for k in PUBLIC_FIELDS}


async def user_count() -> int:
    db = await get_db()
    try:
        async with db.execute("SELECT COUNT(*) AS n FROM users") as cur:
            row = await cur.fetchone()
        return row["n"]
    finally:
        await db.close()


async def needs_setup() -> bool:
    """True until the very first account exists — the admin-claim window."""
    return await user_count() == 0


async def get_user_by_id(user_id: int) -> dict | None:
    db = await get_db()
    try:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def get_user_by_email(email: str) -> dict | None:
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


# Serialises the "is this the first account?" check with the insert that
# answers it. Two people hitting an open Hub's setup screen at the same instant
# would otherwise both read zero users and both become admin.
_create_lock = asyncio.Lock()


async def create_user(
    email: str,
    password: str,
    *,
    name: str = "",
    role: str | None = None,
    status: str | None = None,
) -> dict:
    """Create an account.

    With `role`/`status` left unset the rule is positional: the first account
    ever created owns the Hub (admin, active); everyone after it must be
    approved (user, pending).
    """
    email = validate_email(email)
    validate_password(password)

    async with _create_lock:
        return await _create_user_locked(email, password, name, role, status)


async def _create_user_locked(email, password, name, role, status) -> dict:
    if role is None or status is None:
        first = await needs_setup()
        role = role or ("admin" if first else "user")
        status = status or ("active" if first else "pending")

    db = await get_db()
    try:
        async with db.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ) as cur:
            if await cur.fetchone():
                raise ValueError("An account with that email already exists.")
        reviewed = _now_iso() if status != "pending" else None
        await db.execute(
            """INSERT INTO users (email, name, password_hash, role, status,
                                  api_key, reviewed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email, name.strip(), hash_password(password), role, status,
             new_api_key(), reviewed),
        )
        await db.commit()
        async with db.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row)
    finally:
        await db.close()


async def update_user(user_id: int, **fields) -> dict | None:
    """Patch an account. Unknown keys are ignored; `password` is hashed."""
    allowed = {"email", "name", "role", "status", "password_hash", "api_key",
               "reviewed_at", "last_login_at"}
    if "password" in fields:
        fields["password_hash"] = hash_password(validate_password(fields.pop("password")))
    if "email" in fields and fields["email"] is not None:
        fields["email"] = validate_email(fields["email"])
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return await get_user_by_id(user_id)

    db = await get_db()
    try:
        if "email" in sets:
            async with db.execute(
                "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE AND id != ?",
                (sets["email"], user_id),
            ) as cur:
                if await cur.fetchone():
                    raise ValueError("An account with that email already exists.")
        clause = ", ".join(f"{k} = ?" for k in sets)
        await db.execute(
            f"UPDATE users SET {clause} WHERE id = ?", (*sets.values(), user_id)
        )
        await db.commit()
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    invalidate_caches()
    return dict(row) if row else None


async def delete_user(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    invalidate_caches()


async def admin_count(exclude_id: int | None = None) -> int:
    """Active admins, optionally ignoring one account.

    Used to refuse the last-admin demotion/deletion that would lock everyone
    out of user management with no way back in.
    """
    db = await get_db()
    try:
        sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND status = 'active'"
        params: tuple = ()
        if exclude_id is not None:
            sql += " AND id != ?"
            params = (exclude_id,)
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return row["n"]
    finally:
        await db.close()


# ── sessions ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


#: UTC timestamp in the same format SQLite's datetime('now') produces, so
#: values written from Python sort and compare against it correctly.
now_iso = _now_iso


async def create_session(user_id: int, *, user_agent: str = "", ip: str = "") -> str:
    token = new_session_token()
    expires = datetime.now(timezone.utc) + timedelta(days=settings.session_ttl_days)
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO sessions (token_hash, user_id, expires_at, user_agent, ip)
               VALUES (?, ?, ?, ?, ?)""",
            (token_digest(token), user_id,
             expires.strftime("%Y-%m-%d %H:%M:%S"), user_agent[:256], ip[:64]),
        )
        await db.execute(
            "UPDATE users SET last_login_at = ? WHERE id = ?", (_now_iso(), user_id)
        )
        await db.commit()
    finally:
        await db.close()
    invalidate_caches()
    return token


async def destroy_session(token: str) -> None:
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM sessions WHERE token_hash = ?", (token_digest(token),)
        )
        await db.commit()
    finally:
        await db.close()
    _session_cache.pop(token_digest(token), None)


async def destroy_sessions_for_user(user_id: int) -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        await db.commit()
    finally:
        await db.close()
    invalidate_caches()


async def purge_expired_sessions() -> None:
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM sessions WHERE expires_at < ?", (_now_iso(),)
        )
        await db.commit()
    finally:
        await db.close()


async def user_for_session(token: str) -> dict | None:
    """Resolve a cookie token to its *active* account, or None."""
    if not token:
        return None
    digest = token_digest(token)
    hit = _session_cache.get(digest)
    now = time.time()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    db = await get_db()
    try:
        async with db.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.expires_at > ?""",
            (digest, _now_iso()),
        ) as cur:
            row = await cur.fetchone()
        user = dict(row) if row else None
        if user and user["status"] != "active":
            user = None
        if row is not None:
            await db.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                (_now_iso(), digest),
            )
            await db.commit()
    finally:
        await db.close()

    _session_cache[digest] = (now, user)
    return user


async def user_for_api_key(key: str) -> dict | None:
    """Resolve a bearer/x-api-key value to its *active* account, or None."""
    if not key:
        return None
    hit = _apikey_cache.get(key)
    now = time.time()
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM users WHERE api_key = ? AND status = 'active'", (key,)
        ) as cur:
            row = await cur.fetchone()
    finally:
        await db.close()
    user = dict(row) if row else None
    _apikey_cache[key] = (now, user)
    return user


# ── usage accounting ────────────────────────────────────────────────────────

async def record_usage(
    user_id: int,
    *,
    model: str = "",
    endpoint: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    source: str = "",
) -> None:
    """Append one call to the usage log. Never raises into the request path.

    A failure to *account* for a call must not fail the call itself — the user
    already has their answer from the model by the time this runs.
    """
    if not total_tokens:
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
    try:
        db = await get_db()
        try:
            await db.execute(
                """INSERT INTO usage_events
                   (user_id, model, endpoint, prompt_tokens, completion_tokens,
                    total_tokens, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (user_id, model or "", endpoint or "", prompt_tokens or 0,
                 completion_tokens or 0, total_tokens or 0, source or ""),
            )
            await db.commit()
        finally:
            await db.close()
    except Exception:
        pass


async def usage_summary(user_id: int | None = None) -> dict[int, dict]:
    """Per-account usage totals, keyed by user id.

    Returns lifetime counts plus a 30-day and 24-hour window, so the admin
    list can distinguish "used this a lot once" from "is using this now".
    """
    db = await get_db()
    try:
        where = "WHERE user_id = ?" if user_id is not None else ""
        params: tuple = (user_id,) if user_id is not None else ()
        async with db.execute(
            f"""SELECT user_id,
                       COUNT(*)                  AS requests,
                       SUM(prompt_tokens)        AS prompt_tokens,
                       SUM(completion_tokens)    AS completion_tokens,
                       SUM(total_tokens)         AS total_tokens,
                       MAX(ts)                   AS last_used_at,
                       SUM(CASE WHEN ts >= datetime('now','-30 days') THEN 1 ELSE 0 END)
                                                 AS requests_30d,
                       SUM(CASE WHEN ts >= datetime('now','-30 days') THEN total_tokens ELSE 0 END)
                                                 AS tokens_30d,
                       SUM(CASE WHEN ts >= datetime('now','-1 day') THEN 1 ELSE 0 END)
                                                 AS requests_24h,
                       SUM(CASE WHEN ts >= datetime('now','-1 day') THEN total_tokens ELSE 0 END)
                                                 AS tokens_24h
                FROM usage_events {where} GROUP BY user_id""",
            params,
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return {r["user_id"]: {k: (r[k] or 0) if k != "last_used_at" else r[k]
                           for k in r.keys() if k != "user_id"} for r in rows}


EMPTY_USAGE = {
    "requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
    "total_tokens": 0, "last_used_at": None,
    "requests_30d": 0, "tokens_30d": 0, "requests_24h": 0, "tokens_24h": 0,
}


async def top_models(user_id: int | None = None, limit: int = 5) -> list[dict]:
    db = await get_db()
    try:
        where = "WHERE user_id = ? AND model != ''" if user_id is not None else "WHERE model != ''"
        params: tuple = (user_id, limit) if user_id is not None else (limit,)
        async with db.execute(
            f"""SELECT model, COUNT(*) AS requests, SUM(total_tokens) AS total_tokens
                FROM usage_events {where}
                GROUP BY model ORDER BY requests DESC LIMIT ?""",
            params,
        ) as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()
    return [dict(r) for r in rows]
