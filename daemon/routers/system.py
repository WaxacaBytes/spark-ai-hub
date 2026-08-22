import asyncio
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from daemon.config import settings
from daemon.models.container import SystemMetrics
from daemon.services import hf_token
from daemon.services.connect_service import compute_connect_info
from daemon.services.monitor_service import get_system_metrics

router = APIRouter(tags=["system"])


@router.get("/api/system/connect")
def connect_info(request: Request):
    """Reachable addresses for wiring up a `sah` client to this Hub.

    Sync `def` so FastAPI runs it in a threadpool — it shells out to
    `tailscale` and opens a socket, which must not block the event loop."""
    user = getattr(request.state, "user", None)
    return compute_connect_info(settings.public_port, user["api_key"] if user else None)


@router.get("/api/system/hf-token")
async def get_hf_token():
    return {"has_token": hf_token.has_token()}


class HFTokenBody(BaseModel):
    token: str


@router.post("/api/system/hf-token")
async def set_hf_token(body: HFTokenBody):
    hf_token.write_token(body.token)
    return {"status": "saved"}


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
