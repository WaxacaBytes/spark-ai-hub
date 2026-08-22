import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from daemon.config import settings
from daemon.db import init_db
from daemon.middleware.auth import AuthMiddleware
from daemon.routers import (
    admin, anthropic_proxy, auth, containers, openai_proxy, recipes, system,
)
from daemon.services.connect_service import compute_connect_info
from daemon.services.registry_service import load_recipes, get_recipes
from daemon.services.auth_service import purge_expired_sessions
from daemon.services.docker_service import is_recipe_running, start_health_check
from daemon.services import proxy_service

DIST_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

# Paths ending in one of these are assets, never SPA routes: if the file is
# missing the request must 404 rather than fall through to index.html.
ASSET_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".ico", ".avif",
    ".css", ".js", ".mjs", ".map", ".json", ".woff", ".woff2", ".ttf",
}
SAH_DIR = Path(__file__).resolve().parent.parent / "sah"


async def _check_running_readiness():
    """On startup, probe already-running recipes so ready cache is warm."""
    await asyncio.sleep(2)  # let everything initialize
    slugs = list(get_recipes().keys())
    for slug in slugs:
        if await is_recipe_running(slug):
            await start_health_check(slug)


async def _session_janitor():
    """Drop expired sessions hourly so the table cannot grow without bound."""
    while True:
        try:
            await purge_expired_sessions()
        except Exception:
            pass
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    load_recipes()
    # The front door on :9000. Started after the registry loads, because its
    # config is generated from it -- one /run/{slug}/ route per proxied recipe.
    await proxy_service.ensure_proxy()
    asyncio.create_task(_check_running_readiness())
    asyncio.create_task(_session_janitor())
    yield


app = FastAPI(title="Spark AI Hub", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Added last, so it wraps outermost and runs *before* CORS and every router:
# nothing under /api, /ws or /v1 reaches a handler without an account behind
# it. WebSockets included — hence raw ASGI rather than BaseHTTPMiddleware.
app.add_middleware(AuthMiddleware)

# API and WebSocket routers first — order matters
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(recipes.router)
app.include_router(containers.router)
app.include_router(system.router)
# Anthropic must come before openai_proxy: the latter is a /v1/{path:path}
# catch-all that would otherwise swallow /v1/messages.
app.include_router(anthropic_proxy.router)
app.include_router(openai_proxy.router)

# Serve the `sah` CLI for `curl ${HUB}/sah/install.sh | sh`.
# The installer is served dynamically so it bakes in *this* Hub's own stable
# addresses (mDNS / Tailscale) as the candidate list — never the transient IP
# the client happened to curl from. Registered before the /sah static mount so
# it takes precedence over the raw file.
if SAH_DIR.is_dir():
    _SAH_INSTALL = SAH_DIR / "install.sh"

    @app.get("/sah/install.sh")
    def sah_install_script():
        # Sync def → threadpool: compute_connect_info shells out to tailscale.
        text = _SAH_INSTALL.read_text()
        info = compute_connect_info(settings.public_port)
        candidates = " ".join(c["url"] for c in info["candidates"]) or info["primary"]
        # Replace only the assignment (first occurrence); the guard line keeps
        # the literal marker so a standalone run still detects "not injected".
        text = text.replace("@SAH_CANDIDATES@", candidates, 1)
        return Response(content=text, media_type="text/x-shellscript")

    app.mount("/sah", StaticFiles(directory=str(SAH_DIR)), name="sah")

# The unmodified cover source images, exactly as a recipe shipped them.
# Everything under /covers is a rendered derivative (cropped, graded,
# scrimmed); this is the only place the original picture is served whole.
COVER_SOURCES = Path(__file__).resolve().parent.parent / "registry" / "covers"
if COVER_SOURCES.is_dir():
    app.mount("/cover-sources", StaticFiles(directory=str(COVER_SOURCES)),
              name="cover-sources")

# Serve static assets (js, css, etc.) under /assets
if DIST_DIR.is_dir():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        # Try to serve the exact file first
        file_path = DIST_DIR / full_path
        if full_path and file_path.is_file():
            return FileResponse(file_path)
        # A missing asset must 404, not fall through to index.html. Returning
        # HTML with a 200 for a missing .png/.jpg poisons the browser cache:
        # the bad response sticks around under heuristic freshness and the
        # image stays broken long after the real file lands.
        if Path(full_path).suffix.lower() in ASSET_SUFFIXES:
            return Response(status_code=404)
        # Fall back to index.html for SPA routing. It must always revalidate:
        # it names the hashed bundle, so a cached copy pins the browser to a
        # build that no longer exists on disk. The bundle itself is immutable
        # (its hash is in the filename), so only this file needs the header.
        index = DIST_DIR / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return Response(status_code=404)
