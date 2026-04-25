import json
import pytest
from unittest.mock import AsyncMock
import app.api.ws_print as ws_mod
from starlette.testclient import TestClient
from fastapi import FastAPI


@pytest.fixture(autouse=True)
def reset_ws_state():
    ws_mod._active_client = None
    ws_mod._on_connect = None
    ws_mod._on_ack = None
    yield
    ws_mod._active_client = None
    ws_mod._on_connect = None
    ws_mod._on_ack = None


def make_app():
    app = FastAPI()
    app.include_router(ws_mod.router)
    return app


@pytest.mark.asyncio
async def test_send_print_job_returns_false_when_no_client():
    result = await ws_mod.send_print_job("job-1", "base64data")
    assert result is False


@pytest.mark.asyncio
async def test_send_print_job_sends_json_when_client_connected():
    mock_ws = AsyncMock()
    ws_mod._active_client = mock_ws

    result = await ws_mod.send_print_job("job-1", "pdfbase64")

    assert result is True
    mock_ws.send_text.assert_awaited_once()
    sent = json.loads(mock_ws.send_text.call_args[0][0])
    assert sent["job_id"] == "job-1"
    assert sent["pdf_data"] == "pdfbase64"


def test_second_client_receives_error():
    mock_ws = AsyncMock()
    ws_mod._active_client = mock_ws

    app = make_app()
    with TestClient(app) as client:
        with client.websocket_connect("/ws/print") as ws:
            msg = ws.receive_text()
            data = json.loads(msg)
    assert data["error"] == "already_connected"


def test_on_connect_callback_called():
    app = make_app()
    callback = AsyncMock()
    ws_mod.set_callbacks(on_connect=callback, on_ack=AsyncMock())

    with TestClient(app) as client:
        with client.websocket_connect("/ws/print"):
            pass

    callback.assert_called_once()


def test_on_ack_callback_called():
    app = make_app()
    ack_callback = AsyncMock()
    ws_mod.set_callbacks(on_connect=AsyncMock(), on_ack=ack_callback)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/print") as ws:
            ws.send_text(json.dumps({"job_id": "abc", "status": "done"}))

    ack_callback.assert_called_once_with({"job_id": "abc", "status": "done"})
