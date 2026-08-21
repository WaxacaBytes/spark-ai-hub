"""Administrator-only account management.

This is the one part of the Hub that is not open to every approved account:
who gets in, and what they have been using. Everything else — installing,
launching, stopping, editing compose — stays available to any active user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from daemon.routers.auth import require_admin
from daemon.services import auth_service

router = APIRouter(prefix="/api/admin", tags=["admin"])


class CreateUserBody(BaseModel):
    email: str
    password: str
    name: str = ""
    role: str = "user"
    # Admin-created accounts are usable immediately — the admin *is* the
    # approval. Pass status="pending" to stage one for later instead.
    status: str = "active"


class UpdateUserBody(BaseModel):
    name: str | None = None
    email: str | None = None
    password: str | None = None
    role: str | None = None
    status: str | None = None


def _validate_enums(role: str | None, status: str | None) -> None:
    if role is not None and role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="Role must be admin or user.")
    if status is not None and status not in ("pending", "active", "rejected"):
        raise HTTPException(
            status_code=400, detail="Status must be pending, active or rejected."
        )


@router.get("/users")
async def list_users(admin: dict = Depends(require_admin)):
    """Every account, each with its usage totals.

    Pending accounts come first — they are the ones waiting on the admin to do
    something — then the rest by heaviest usage, so the list answers "who needs
    me?" and "who is actually using this?" in one screen.
    """
    from daemon.db import get_db

    db = await get_db()
    try:
        async with db.execute("SELECT * FROM users") as cur:
            rows = await cur.fetchall()
    finally:
        await db.close()

    usage = await auth_service.usage_summary()
    users = [
        {
            **auth_service.public_user(r),
            "usage": usage.get(r["id"], auth_service.EMPTY_USAGE),
        }
        for r in rows
    ]
    users.sort(
        key=lambda u: (
            0 if u["status"] == "pending" else 1,
            -(u["usage"]["total_tokens"] or 0),
            u["email"].lower(),
        )
    )

    totals = {
        "users": len(users),
        "pending": sum(1 for u in users if u["status"] == "pending"),
        "active": sum(1 for u in users if u["status"] == "active"),
        "requests": sum(u["usage"]["requests"] for u in users),
        "total_tokens": sum(u["usage"]["total_tokens"] for u in users),
    }
    return {"users": users, "totals": totals, "models": await auth_service.top_models()}


@router.post("/users", status_code=201)
async def create_user(body: CreateUserBody, admin: dict = Depends(require_admin)):
    _validate_enums(body.role, body.status)
    try:
        user = await auth_service.create_user(
            body.email, body.password,
            name=body.name, role=body.role, status=body.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        **auth_service.public_user(user),
        "usage": auth_service.EMPTY_USAGE,
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: int, body: UpdateUserBody, admin: dict = Depends(require_admin)
):
    """Approve, reject, promote, rename, or reset the password of an account."""
    _validate_enums(body.role, body.status)
    target = await auth_service.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such account.")

    # Refuse the change that would leave nobody able to manage accounts.
    losing_admin = (
        target["role"] == "admin"
        and target["status"] == "active"
        and (body.role == "user" or body.status in ("pending", "rejected"))
    )
    if losing_admin and await auth_service.admin_count(exclude_id=user_id) == 0:
        raise HTTPException(
            status_code=400,
            detail="This is the only administrator — promote someone else first.",
        )

    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "status" in fields and fields["status"] != target["status"]:
        fields["reviewed_at"] = auth_service.now_iso()

    try:
        updated = await auth_service.update_user(user_id, **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # An account that just lost access must lose its live browser sessions too,
    # otherwise "rejected" only takes effect the next time they sign in.
    if updated["status"] != "active" or "password" in fields:
        await auth_service.destroy_sessions_for_user(user_id)

    usage = await auth_service.usage_summary(user_id)
    return {
        **auth_service.public_user(updated),
        "usage": usage.get(user_id, auth_service.EMPTY_USAGE),
    }


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: dict = Depends(require_admin)):
    target = await auth_service.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="No such account.")
    if user_id == admin["id"]:
        raise HTTPException(
            status_code=400, detail="You cannot delete your own account."
        )
    if (
        target["role"] == "admin"
        and await auth_service.admin_count(exclude_id=user_id) == 0
    ):
        raise HTTPException(
            status_code=400,
            detail="This is the only administrator — promote someone else first.",
        )
    await auth_service.delete_user(user_id)
    return {"status": "deleted"}


@router.get("/users/{user_id}/usage")
async def user_usage(user_id: int, admin: dict = Depends(require_admin)):
    if not await auth_service.get_user_by_id(user_id):
        raise HTTPException(status_code=404, detail="No such account.")
    usage = await auth_service.usage_summary(user_id)
    return {
        "usage": usage.get(user_id, auth_service.EMPTY_USAGE),
        "models": await auth_service.top_models(user_id),
    }
