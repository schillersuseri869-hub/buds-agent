import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_print_clients: list[WebSocket] = []


@router.websocket("/ws/print")
async def websocket_print(websocket: WebSocket):
    await websocket.accept()
    _print_clients.append(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            # TODO(print_agent): handle ACK {"job_id": ..., "status": "done"|"failed"}
            ack = json.loads(raw)
            print(f"Print ACK received: {ack}")
    except WebSocketDisconnect:
        _print_clients.remove(websocket)


async def send_print_job(job: dict) -> bool:
    """Send print job to the first connected print_client. Returns False if no client connected."""
    if not _print_clients:
        return False
    await _print_clients[0].send_text(json.dumps(job))
    return True
