import functools
import hashlib
import json
import pathlib

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.templating import Jinja2Templates

from src.broadcast import broadcaster
from src.db import get_chapel_state
from src.week import get_current_service_week

router = APIRouter()

TEMPLATES_DIR = pathlib.Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = TEMPLATES_DIR.parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@functools.cache
def static_url(path: str) -> str:
    """A static file's URL, fingerprinted with its contents. Cloudflare caches /static for
    hours, so without a new URL a deploy's CSS or JS could reach visitors hours late."""
    digest = hashlib.sha256((STATIC_DIR / path).read_bytes()).hexdigest()[:12]
    return f"/static/{path}?v={digest}"


templates.env.globals["static_url"] = static_url


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
