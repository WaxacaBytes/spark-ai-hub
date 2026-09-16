"""OAuth endpoints for /mcp: discovery, registration, sign-in/consent, tokens.

The consent page is served here as plain HTML rather than as a route in the
SPA: it has to work for someone who arrives from claude.ai with no Hub tab
open, and it must not be framable. It signs in with the Hub's own accounts.

Everything is derived from the address the request came in on, so the same
Hub is a valid issuer at its tunnel hostname, its Tailscale name and its LAN
address alike — Claude only ever sees the one it was given.
"""
from __future__ import annotations

import html
import secrets
import time
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from daemon.config import settings
from daemon.routers.auth import set_session_cookie
from daemon.services import auth_service, oauth_service
from daemon.services.connect_service import client_ip, request_origin
from daemon.services.oauth_service import OAuthError

router = APIRouter(tags=["oauth"])

MCP_PATH = "/mcp"
REQUEST_TTL = 600

# Authorization requests between the page render and the Allow click, keyed by
# a random id. Each one is bound to the session that saw the consent page, so a
# forged POST from another site (which carries no cookie under SameSite=lax)
# cannot approve someone else's request.
_pending: dict[str, dict] = {}


def origin_of(request: Request) -> str:
    return request_origin(request) or f"{request.url.scheme}://{request.url.netloc}"


def resource_metadata_url(origin: str) -> str:
    return f"{origin}/.well-known/oauth-protected-resource{MCP_PATH}"


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


# ── discovery ───────────────────────────────────────────────────────────────


@router.get("/.well-known/oauth-protected-resource")
@router.get(f"/.well-known/oauth-protected-resource{MCP_PATH}")
async def protected_resource_metadata(request: Request):
    origin = origin_of(request)
    return {
        "resource": f"{origin}{MCP_PATH}",
        "authorization_servers": [origin],
        "scopes_supported": [oauth_service.SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Spark AI Hub",
    }


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request):
    origin = origin_of(request)
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "registration_endpoint": f"{origin}/oauth/register",
        "scopes_supported": [oauth_service.SCOPE],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


@router.api_route("/.well-known/{rest:path}", methods=["GET", "HEAD"])
async def well_known_missing(rest: str):
    # Without this the SPA fallback answers every /.well-known path with its
    # HTML and a 200, which a discovery client reads as a broken document.
    return JSONResponse({"error": "not_found"}, status_code=404)


# ── registration ────────────────────────────────────────────────────────────


@router.post("/oauth/register")
async def register(request: Request):
    try:
        meta = await request.json()
        if not isinstance(meta, dict):
            raise ValueError
    except ValueError:
        return JSONResponse({"error": "invalid_client_metadata",
                             "error_description": "Body must be a JSON object."}, status_code=400)
    try:
        client = await oauth_service.register_client(meta)
    except OAuthError as exc:
        return JSONResponse(exc.body(), status_code=exc.status)
    return _no_store(JSONResponse(client, status_code=201))


# ── token ───────────────────────────────────────────────────────────────────


async def _form(request: Request) -> dict[str, str]:
    body = (await request.body()).decode(errors="replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(body, keep_blank_values=True).items()}


@router.post("/oauth/token")
async def token(request: Request):
    if "application/x-www-form-urlencoded" not in request.headers.get("content-type", ""):
        return _no_store(JSONResponse({"error": "invalid_request",
                                       "error_description": "Use application/x-www-form-urlencoded."},
                                      status_code=400))
    form = await _form(request)
    client_id = form.get("client_id", "")
    try:
        if not await oauth_service.get_client(client_id):
            raise OAuthError("invalid_client", "Unknown client_id.", status=401)
        grant = form.get("grant_type")
        if grant == "authorization_code":
            result = await oauth_service.exchange_code(
                code=form.get("code", ""), client_id=client_id,
                redirect_uri=form.get("redirect_uri", ""),
                code_verifier=form.get("code_verifier", ""),
                resource=form.get("resource") or None,
            )
        elif grant == "refresh_token":
            result = await oauth_service.refresh(
                refresh_token=form.get("refresh_token", ""), client_id=client_id,
                resource=form.get("resource") or None,
            )
        else:
            raise OAuthError("unsupported_grant_type", f"grant_type {grant!r} is not supported.")
    except OAuthError as exc:
        return _no_store(JSONResponse(exc.body(), status_code=exc.status))
    return _no_store(JSONResponse(result))


# ── authorize: sign in, consent, redirect ───────────────────────────────────


def _redirect_with(redirect_uri: str, params: dict) -> str:
    parts = urllib.parse.urlsplit(redirect_uri)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query += [(k, v) for k, v in params.items() if v is not None]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · Spark AI Hub</title>
<style>
:root{{color-scheme:dark}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d10;
color:#e6e8eb;font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif;padding:16px}}
.card{{width:100%;max-width:420px;background:#14171c;border:1px solid #262b33;border-radius:14px;padding:28px}}
h1{{font-size:20px;margin:0 0 6px}} p{{margin:8px 0;color:#aab1bb}} b{{color:#e6e8eb}}
.brand{{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#76b900;margin-bottom:14px}}
label{{display:block;font-size:13px;color:#aab1bb;margin:14px 0 4px}}
input{{width:100%;box-sizing:border-box;padding:10px 12px;border-radius:8px;border:1px solid #2f3540;
background:#0b0d10;color:#e6e8eb;font:inherit}}
.row{{display:flex;gap:10px;margin-top:22px}}
button{{flex:1;padding:11px;border-radius:8px;border:1px solid #2f3540;background:#1c2128;color:#e6e8eb;
font:600 14px system-ui,sans-serif;cursor:pointer}}
button.primary{{background:#76b900;border-color:#76b900;color:#0b0d10}}
.err{{background:#3a1717;border:1px solid #6b2a2a;color:#ffb4b4;border-radius:8px;padding:8px 12px;margin-top:12px}}
ul{{margin:10px 0;padding-left:20px;color:#aab1bb}} code{{color:#e6e8eb}}
</style></head><body><main class="card"><div class="brand">Spark AI Hub</div>{body}</main></body></html>"""
    response = HTMLResponse(doc, status_code=status)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
    response.headers["Referrer-Policy"] = "no-referrer"
    return _no_store(response)


def _error_page(message: str, status: int = 400) -> HTMLResponse:
    return _page("Can't connect", f"<h1>Can't connect</h1><p>{html.escape(message)}</p>", status)


def _login_page(pending_id: str, pending: dict, error: str = "") -> HTMLResponse:
    err = f'<div class="err">{html.escape(error)}</div>' if error else ""
    return _page("Sign in", f"""
<h1>Sign in to continue</h1>
<p><b>{html.escape(pending['client_name'])}</b> wants to connect to this Hub. Sign in with your Spark AI Hub account.</p>
{err}
<form method="post" action="/oauth/authorize">
<input type="hidden" name="request_id" value="{html.escape(pending_id)}">
<input type="hidden" name="action" value="login">
<label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required autofocus>
<label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<div class="row"><button class="primary" type="submit">Sign in</button></div>
</form>""", status=401 if error else 200)


def _consent_page(pending_id: str, pending: dict, user: dict) -> HTMLResponse:
    host = urllib.parse.urlsplit(pending["redirect_uri"]).hostname or pending["redirect_uri"]
    return _page("Allow access", f"""
<h1>Allow {html.escape(pending['client_name'])}?</h1>
<p>Signed in as <b>{html.escape(user['email'])}</b>.</p>
<p>It will be able to:</p>
<ul><li>Generate and edit images with the models running on this Hub</li>
<li>See which image models are installed</li></ul>
<p>Access is sent back to <code>{html.escape(host)}</code>. It never gets your password or API key.</p>
<form method="post" action="/oauth/authorize">
<input type="hidden" name="request_id" value="{html.escape(pending_id)}">
<div class="row"><button type="submit" name="action" value="deny">Deny</button>
<button class="primary" type="submit" name="action" value="allow">Allow</button></div>
</form>""")


def _session_digest(request: Request) -> str | None:
    token = request.cookies.get(settings.session_cookie_name)
    return auth_service.token_digest(token) if token else None


def _prune_pending() -> None:
    cutoff = time.time() - REQUEST_TTL
    for key in [k for k, v in _pending.items() if v["created"] < cutoff]:
        _pending.pop(key, None)


@router.get("/oauth/authorize")
async def authorize(request: Request):
    params = request.query_params
    client = await oauth_service.get_client(params.get("client_id", ""))
    if not client:
        return _error_page("This app is not registered with the Hub. Remove the connector and add it again.")

    redirect_uri = params.get("redirect_uri") or (
        client["redirect_uris"][0] if len(client["redirect_uris"]) == 1 else "")
    if not redirect_uri or not oauth_service.redirect_matches(client["redirect_uris"], redirect_uri):
        # Never redirect to a URI the client did not register: that is how a
        # code would be delivered to an attacker.
        return _error_page("The app asked to return to an address it did not register.")

    origin = origin_of(request)
    state = params.get("state")

    def fail(error: str, description: str) -> RedirectResponse:
        return RedirectResponse(_redirect_with(redirect_uri, {
            "error": error, "error_description": description, "state": state, "iss": origin,
        }), status_code=302)

    if params.get("response_type") != "code":
        return fail("unsupported_response_type", "Only response_type=code is supported.")
    if not params.get("code_challenge") or params.get("code_challenge_method") != "S256":
        return fail("invalid_request", "PKCE with code_challenge_method=S256 is required.")
    resource = (params.get("resource") or f"{origin}{MCP_PATH}").rstrip("/")
    if resource != f"{origin}{MCP_PATH}":
        return fail("invalid_target", f"The only resource here is {origin}{MCP_PATH}.")

    _prune_pending()
    pending_id = secrets.token_urlsafe(24)
    user = request.state.user if getattr(request.state, "auth_source", "") == "session" else None
    pending = {
        "client_id": client["client_id"],
        "client_name": client["client_name"] or "An app",
        "redirect_uri": redirect_uri,
        "code_challenge": params["code_challenge"],
        "resource": resource,
        "scope": oauth_service.SCOPE,
        "state": state,
        "origin": origin,
        "created": time.time(),
        "session": _session_digest(request) if user else None,
        "user_id": user["id"] if user else None,
    }
    _pending[pending_id] = pending
    return _consent_page(pending_id, pending, user) if user else _login_page(pending_id, pending)


@router.post("/oauth/authorize")
async def authorize_decision(request: Request):
    form = await _form(request)
    pending_id = form.get("request_id", "")
    pending = _pending.get(pending_id)
    if not pending or time.time() - pending["created"] > REQUEST_TTL:
        _pending.pop(pending_id, None)
        return _error_page("This sign-in request expired. Start connecting again from the app.")

    action = form.get("action")
    if action == "login":
        user = await auth_service.get_user_by_email(form.get("email", ""))
        if not user or not auth_service.verify_password(form.get("password", ""), user["password_hash"]):
            return _login_page(pending_id, pending, "Incorrect email or password.")
        if user["status"] != "active":
            return _login_page(pending_id, pending, "This account is not active yet.")
        token = await auth_service.create_session(
            user["id"], user_agent=request.headers.get("user-agent", ""),
            ip=client_ip(request) or "",
        )
        pending["session"] = auth_service.token_digest(token)
        pending["user_id"] = user["id"]
        response = _consent_page(pending_id, pending, user)
        set_session_cookie(response, request, token)
        return response

    user = request.state.user if getattr(request.state, "auth_source", "") == "session" else None
    if (not user or pending["session"] is None or _session_digest(request) != pending["session"]
            or user["id"] != pending["user_id"]):
        return _error_page("Sign in on this page to approve the connection.", status=403)

    _pending.pop(pending_id, None)
    if action != "allow":
        return RedirectResponse(_redirect_with(pending["redirect_uri"], {
            "error": "access_denied", "error_description": "The user denied access.",
            "state": pending["state"], "iss": pending["origin"],
        }), status_code=303)

    code = await oauth_service.create_code(
        client_id=pending["client_id"], user_id=user["id"],
        redirect_uri=pending["redirect_uri"], code_challenge=pending["code_challenge"],
        resource=pending["resource"], scope=pending["scope"],
    )
    return RedirectResponse(_redirect_with(pending["redirect_uri"], {
        "code": code, "state": pending["state"], "iss": pending["origin"],
    }), status_code=303)
