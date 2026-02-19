import asyncio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from daemon.models.container import SystemMetrics
from daemon.services.monitor_service import get_system_metrics

router = APIRouter(tags=["system"])


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
