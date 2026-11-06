import asyncio
import json
import os

from fastapi import WebSocket

from src.ratelimit import address_key

# The chapel is a public page, so bound what any number of open tabs can cost.
MAX_VIEWERS = int(os.environ.get("CHAPEL_MAX_VIEWERS", 500))
MAX_PER_ADDRESS = int(os.environ.get("CHAPEL_MAX_VIEWERS_PER_ADDRESS", 10))
SEND_TIMEOUT = 5  # seconds; a viewer slower than this is dropped


class ChapelBroadcaster:
    def __init__(self, max_viewers: int = MAX_VIEWERS, max_per_address: int = MAX_PER_ADDRESS):
        self.max_viewers = max_viewers
        self.max_per_address = max_per_address
        self.connections: dict[WebSocket, str] = {}  # viewer -> address key

    async def connect(self, ws: WebSocket) -> bool:
        """Accept a viewer, or turn it away (close code 1013, "try again later") when the chapel
        is full, or when one address already holds its share of the seats."""
        await ws.accept()
        key = address_key(ws.client.host) if ws.client else ""
        same = sum(1 for k in self.connections.values() if k == key)
        if len(self.connections) >= self.max_viewers or same >= self.max_per_address:
            await ws.close(code=1013)
            return False
        self.connections[ws] = key
        return True

    def disconnect(self, ws: WebSocket):
        self.connections.pop(ws, None)

    async def _send(self, ws: WebSocket, message: str) -> None:
        try:
            await asyncio.wait_for(ws.send_text(message), SEND_TIMEOUT)
        except Exception:
            self.disconnect(ws)

    async def broadcast(self, event: dict):
        message = json.dumps(event)
        # Concurrently, so one slow viewer can't hold up the rest (or the service that triggered this).
        await asyncio.gather(*(self._send(ws, message) for ws in list(self.connections)))


broadcaster = ChapelBroadcaster()
