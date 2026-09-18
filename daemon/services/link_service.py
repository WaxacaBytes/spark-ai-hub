"""Short-lived links that move files between an agent's workspace and the Hub.

An agent in a sandbox (Claude Desktop, claude.ai, a cloud agent) can run curl
but must never hold the user's Hub key. So the key stays in the MCP connection,
and the agent asks for a link over MCP:

* create_upload -> POST /link/upload/<token>: one file, into the account that
  asked. The token is bound to that account when it is made, so the file has
  its owner from the moment it is saved. A successful upload uses the link up;
  a refused one (not an image, too big) does not, so the agent can retry.
* create_download -> GET /link/download/<token>: one file the account owns,
  as often as needed until the link expires (downloads get retried).

The link is the only credential, and it can do exactly that one thing. It
expires after LINK_TTL because it lands in chat transcripts and logs; the
agent just asks for a fresh one. Only the token's SHA-256 is stored, like
sessions and OAuth tokens.
"""
from __future__ import annotations

import hashlib
import secrets
import time

from daemon.db import get_db

LINK_TTL = 15 * 60
UPLOAD_PREFIX = "/link/upload/"
DOWNLOAD_PREFIX = "/link/download/"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def create(kind: str, user_id: int, name: str = "") -> tuple[str, int]:
    """A new `kind` ('upload' | 'download') link for account `user_id`."""
    token = secrets.token_urlsafe(32)
    expires = int(time.time()) + LINK_TTL
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO media_links (token_hash, kind, user_id, name, expires_at) VALUES (?, ?, ?, ?, ?)",
            (_hash(token), kind, user_id, name, expires))
        await db.commit()
    finally:
        await db.close()
    return token, expires


async def _owner(db, token_hash: str) -> dict | None:
    row = await (await db.execute(
        "SELECT u.id, u.role FROM media_links l JOIN users u ON u.id = l.user_id "
        "WHERE l.token_hash = ? AND u.status = 'active'", (token_hash,))).fetchone()
    return {"id": row["id"], "role": row["role"]} if row else None


async def claim_upload(token: str) -> dict | None:
    """Reserve an upload link; returns its owner, or None if it is not usable.

    The reservation is one UPDATE, so two requests racing on one link cannot
    both get it. release_upload() hands it back if the upload then fails.
    """
    now, token_hash = int(time.time()), _hash(token)
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE media_links SET used_at = ? WHERE token_hash = ? AND kind = 'upload' "
            "AND used_at IS NULL AND expires_at > ?", (now, token_hash, now))
        await db.commit()
        if cur.rowcount != 1:
            return None
        owner = await _owner(db, token_hash)
        if owner is None:           # the account was suspended since
            await db.execute("UPDATE media_links SET used_at = NULL WHERE token_hash = ?", (token_hash,))
            await db.commit()
        return owner
    finally:
        await db.close()


async def release_upload(token: str) -> None:
    db = await get_db()
    try:
        await db.execute("UPDATE media_links SET used_at = NULL WHERE token_hash = ? AND kind = 'upload'",
                         (_hash(token),))
        await db.commit()
    finally:
        await db.close()


async def download_target(token: str) -> tuple[str, dict] | None:
    """(file name, owner) for a live download link, else None."""
    token_hash = _hash(token)
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT name FROM media_links WHERE token_hash = ? AND kind = 'download' AND expires_at > ?",
            (token_hash, int(time.time())))).fetchone()
        if row is None:
            return None
        owner = await _owner(db, token_hash)
    finally:
        await db.close()
    return (row["name"], owner) if owner else None


async def purge_expired() -> None:
    db = await get_db()
    try:
        await db.execute("DELETE FROM media_links WHERE expires_at <= ?", (int(time.time()),))
        await db.commit()
    finally:
        await db.close()
