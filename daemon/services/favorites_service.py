"""Favorites: the builds an admin starred as the ones to use on this Spark.

A star is this Hub's own choice, so it lives in the database with who set it
and when, never in a recipe: the catalog states facts and leaves "best" to
whoever runs the Spark. Whatever picks a model when none is named puts
starred ones first: the /v1 default, the order of /v1/models (which sah's
pickers follow), the MCP model lists and the render tools' choice of a
running model. The stars are also held in memory, because the /v1 router
reads them on every request.
"""

from daemon.db import get_db

_stars: dict[str, dict] = {}


async def load() -> None:
    db = await get_db()
    try:
        rows = await (await db.execute(
            """SELECT f.slug, f.set_at, COALESCE(NULLIF(u.name, ''), u.email, '') AS by_name
               FROM favorites f LEFT JOIN users u ON u.id = f.set_by"""
        )).fetchall()
    finally:
        await db.close()
    _stars.clear()
    _stars.update({r["slug"]: {"by": r["by_name"], "at": r["set_at"]} for r in rows})


def is_favorite(slug: str) -> bool:
    return slug in _stars


def star_of(slug: str) -> dict | None:
    """Who starred `slug` and when (UTC, SQLite datetime), or None."""
    return _stars.get(slug)


async def set_favorite(slug: str, user: dict, on: bool) -> None:
    db = await get_db()
    try:
        if on:
            await db.execute(
                "INSERT OR REPLACE INTO favorites (slug, set_by) VALUES (?, ?)", (slug, user["id"]))
        else:
            await db.execute("DELETE FROM favorites WHERE slug = ?", (slug,))
        await db.commit()
    finally:
        await db.close()
    await load()
