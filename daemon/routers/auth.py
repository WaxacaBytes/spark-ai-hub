"""Sign-in, sign-up, and self-service account management."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from daemon.config import settings
from daemon.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── dependencies ────────────────────────────────────────────────────────────

def current_user(request: Request) -> dict:
    """The signed-in account, or 401.

    The middleware has already resolved and attached it; this only turns the
    absence into the right HTTP error for a route that cannot run without one.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=403, detail="Only an administrator can do that."
        )
    return user


# ── cookie handling ─────────────────────────────────────────────────────────

def _is_https(request: Request) -> bool:
    """Did this request reach the user over TLS?

    Behind a Cloudflare Tunnel (or Caddy/nginx) the daemon is spoken to in
    plain HTTP, so the only honest signal is the forwarded header. Getting this
    wrong in the strict direction is worse than it sounds: a Secure cookie set
    for a plain-HTTP LAN visit is silently dropped by the browser and the user
    can never sign in.
    """
    if settings.force_secure_cookie:
        return True
    proto = request.headers.get("x-forwarded-proto", "")
    if proto:
        return proto.split(",")[0].strip().lower() == "https"
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,        # JS cannot read it, so an XSS cannot steal it
        samesite="lax",       # blocks cross-site POSTs — this is the CSRF defence
        secure=_is_https(request),
        path="/",
    )


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        settings.session_cookie_name,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_is_https(request),
    )


# ── payloads ────────────────────────────────────────────────────────────────

class Credentials(BaseModel):
    email: str
    password: str
    name: str = ""


class LoginBody(BaseModel):
    email: str
    password: str


class UpdateMeBody(BaseModel):
    name: str | None = None
    email: str | None = None
    current_password: str | None = None
    new_password: str | None = None


# ── routes ──────────────────────────────────────────────────────────────────

@router.get("/state")
async def auth_state(request: Request):
    """What the sign-in screen needs before anyone is signed in.

    `needs_setup` is true only while the Hub has no accounts at all — that is
    the window in which the first visitor claims it as administrator.
    """
    user = getattr(request.state, "user", None)
    return {
        "auth_enabled": settings.auth_enabled,
        "needs_setup": await auth_service.needs_setup(),
        "user": auth_service.public_user(user) if user else None,
    }


@router.post("/register")
async def register(body: Credentials, request: Request, response: Response):
    """Self-service sign-up.

    The first account ever created is the administrator and is signed in on the
    spot. Every later one is created 'pending' and gets no session until an
    admin approves it.
    """
    try:
        user = await auth_service.create_user(
            body.email, body.password, name=body.name
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if user["status"] != "active":
        return {
            "status": "pending",
            "message": "Your account was created and is waiting for an "
                       "administrator to approve it.",
            "user": auth_service.public_user(user),
        }

    token = await auth_service.create_session(
        user["id"],
        user_agent=request.headers.get("user-agent", ""),
        ip=_client_ip(request),
    )
    set_session_cookie(response, request, token)
    return {"status": "active", "user": auth_service.public_user(user)}


@router.post("/login")
async def login(body: LoginBody, request: Request, response: Response):
    user = await auth_service.get_user_by_email(body.email)
    # One message for "no such account" and for "wrong password": telling them
    # apart is a free account-enumeration oracle on an internet-facing login.
    if not user or not auth_service.verify_password(
        body.password, user["password_hash"]
    ):
        raise HTTPException(status_code=401, detail="Incorrect email or password.")

    if user["status"] == "pending":
        raise HTTPException(
            status_code=403,
            detail="Your account is waiting for an administrator to approve it.",
        )
    if user["status"] != "active":
        raise HTTPException(
            status_code=403, detail="This account has been disabled."
        )

    token = await auth_service.create_session(
        user["id"],
        user_agent=request.headers.get("user-agent", ""),
        ip=_client_ip(request),
    )
    set_session_cookie(response, request, token)
    return {"status": "active", "user": auth_service.public_user(user)}


@router.post("/logout")
async def logout(request: Request, response: Response):
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        await auth_service.destroy_session(token)
    clear_session_cookie(response, request)
    return {"status": "signed_out"}


@router.get("/me")
async def me(user: dict = Depends(current_user)):
    usage = await auth_service.usage_summary(user["id"])
    return {
        **auth_service.public_user(user),
        "api_key": user["api_key"],
        "usage": usage.get(user["id"], auth_service.EMPTY_USAGE),
    }


@router.patch("/me")
async def update_me(
    body: UpdateMeBody, request: Request, user: dict = Depends(current_user)
):
    """Change your own name, email or password.

    Email and password changes both re-ask for the current password: a stolen
    or borrowed session must not be enough to take the account over.
    """
    fields: dict = {}
    if body.name is not None:
        fields["name"] = body.name

    wants_sensitive = bool(body.email or body.new_password)
    if wants_sensitive:
        if not body.current_password or not auth_service.verify_password(
            body.current_password, user["password_hash"]
        ):
            raise HTTPException(
                status_code=403, detail="Your current password is incorrect."
            )
        if body.email:
            fields["email"] = body.email
        if body.new_password:
            fields["password"] = body.new_password

    try:
        updated = await auth_service.update_user(user["id"], **fields)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return auth_service.public_user(updated)


@router.post("/me/api-key/rotate")
async def rotate_api_key(user: dict = Depends(current_user)):
    """Issue a new API key, immediately invalidating the old one."""
    updated = await auth_service.update_user(
        user["id"], api_key=auth_service.new_api_key()
    )
    return {"api_key": updated["api_key"]}


@router.get("/me/usage")
async def my_usage(user: dict = Depends(current_user)):
    usage = await auth_service.usage_summary(user["id"])
    return {
        "usage": usage.get(user["id"], auth_service.EMPTY_USAGE),
        "models": await auth_service.top_models(user["id"]),
    }


def _client_ip(request: Request) -> str:
    """Caller's address, preferring what the tunnel/proxy says it was."""
    for header in ("cf-connecting-ip", "x-forwarded-for", "x-real-ip"):
        val = request.headers.get(header)
        if val:
            return val.split(",")[0].strip()
    return request.client.host if request.client else ""
