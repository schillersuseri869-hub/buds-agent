import json
import logging
from typing import Callable, Awaitable
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# Single-process assumption: module-level globals are safe under asyncio's
# single-threaded event loop. Multi-worker deployments would need Redis-backed state.

router = APIRouter()
logger = logging.getLogger(__name__)

_active_client: WebSocket | None = None
_on_connect: Callable[[], Awaitable[None]] | None = None
_on_ack: Callable[[dict], Awaitable[None]] | None = None


def set_callbacks(
    on_connect: Callable[[], Awaitable[None]],
    on_ack: Callable[[dict], Awaitable[None]],
) -> None:
    global _on_connect, _on_ack
    _on_connect, _on_ack = on_connect, on_ack


async def send_print_job(job_id: str, pdf_b64: str) -> bool:
    if _active_client is None:
        return False
    try:
        await _active_client.send_text(
            json.dumps({"job_id": job_id, "pdf_data": pdf_b64})
        )
        return True
    except Exception:
        return False


@router.websocket("/ws/print")
async def websocket_print(websocket: WebSocket):
    global _active_client
    if _active_client is not None:
        await websocket.accept()
        await websocket.send_text(json.dumps({"error": "already_connected"}))
        await websocket.close()
        return
    await websocket.accept()
    _active_client = websocket
    if _on_connect is not None:
        await _on_connect()
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                ack = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Received malformed JSON from print client, skipping")
                continue
            if _on_ack is not None:
                await _on_ack(ack)
    except WebSocketDisconnect:
        _active_client = None
