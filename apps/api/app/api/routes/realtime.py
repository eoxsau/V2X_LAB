import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.realtime_payload import build_realtime_payload
from app.simulation.state import get_state

router = APIRouter(tags=["realtime"])


@router.websocket("/ws/simulation")
async def simulation_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            payload = build_realtime_payload(get_state())
            await websocket.send_json(payload)
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
