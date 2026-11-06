import json
import pathlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates

from src.broadcast import broadcaster
from src.db import get_chapel_state
from src.week import get_current_service_week

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.websocket("/ws/chapel")
async def chapel_websocket(websocket: WebSocket):
    if not await broadcaster.connect(websocket):
        return
    try:
        # Send current chapel state on connect
        service_week = get_current_service_week()
        agents = await get_chapel_state(service_week)
        await websocket.send_text(json.dumps({
            "type": "chapel_state",
            "agents": agents,
            "service_week": service_week,
        }))
        # Keep connection alive, listen for client disconnect
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.disconnect(websocket)
