import asyncio
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from daemon.config import settings
from daemon.models.container import SystemMetrics
from daemon.services import hf_token, registry_service
from daemon.services.connect_service import (
    client_ip,
    compute_connect_info,
    request_origin,
)
from daemon.services.monitor_service import get_system_metrics

router = APIRouter(tags=["system"])


@router.get("/api/system/connect")
def connect_info(request: Request):
    """Reachable addresses for wiring up a `sah` client to this Hub.

    The address the browser is on comes off this very request, so a Hub
    reached through a tunnel hands out the tunnel's URL rather than a LAN
    name nobody out there can resolve. The caller's source address comes off
    it too, so the reply also says whether that caller is on this box's LAN —
    which is how `sah` learns to stop routing local traffic over the internet
    after the Hub's IP has changed.

    Sync `def` so FastAPI runs it in a threadpool — it shells out to
    `tailscale` and opens a socket, which must not block the event loop."""
    user = getattr(request.state, "user", None)
    return compute_connect_info(
        settings.public_port,
        user["api_key"] if user else None,
        origin=request_origin(request),
        caller_ip=client_ip(request),
    )


@router.get("/api/system/hf-token")
async def get_hf_token():
    return {"has_token": hf_token.has_token()}


class HFTokenBody(BaseModel):
    token: str


@router.post("/api/system/hf-token")
async def set_hf_token(body: HFTokenBody):
    hf_token.write_token(body.token)
    return {"status": "saved"}


@router.get("/api/system/hf-access/{slug}")
async def get_hf_access(slug: str):
    """Can the stored token download this recipe's gated repos?

    Answers before an install rather than after: a terms gate is invisible in
    the token and only surfaces as a 403 in the weights stage, which for these
    recipes is an hour of downloading in.
    """
    recipe = registry_service.get_recipe(slug)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    repos = await hf_token.check_repo_access(recipe.gated_repos)
    return {"ok": all(r["accessible"] for r in repos), "repos": repos}


@router.get("/api/system/metrics", response_model=SystemMetrics)
async def metrics():
    return await get_system_metrics()


@router.websocket("/ws/metrics")
async def metrics_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            m = await get_system_metrics()
            await websocket.send_json(m.model_dump())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
