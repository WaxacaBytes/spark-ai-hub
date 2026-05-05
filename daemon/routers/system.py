import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from daemon.models.container import SystemMetrics
from daemon.services import hf_token
from daemon.services.monitor_service import get_system_metrics

router = APIRouter(tags=["system"])


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
