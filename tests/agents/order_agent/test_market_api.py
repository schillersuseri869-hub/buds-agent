import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.order_agent.market_api import set_order_ready, get_order_status


def _mock_client(method: str, status_code: int = 200, json_body: dict = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    if status_code >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
        )
    else:
        resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    setattr(client, method, AsyncMock(return_value=resp))
    return client


@pytest.mark.asyncio
async def test_set_order_ready_sends_put_with_correct_body():
    mock = _mock_client("put")
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        await set_order_ready("YM-123", 148807227, "tok")
    mock.put.assert_awaited_once()
    url, = mock.put.call_args[0]
    assert "/campaigns/148807227/orders/YM-123/status" in url
    assert mock.put.call_args[1]["json"] == {"order": {"status": "READY_TO_SHIP"}}


@pytest.mark.asyncio
async def test_set_order_ready_raises_on_http_error():
    mock = _mock_client("put", status_code=400)
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        with pytest.raises(httpx.HTTPStatusError):
            await set_order_ready("YM-123", 148807227, "tok")


@pytest.mark.asyncio
async def test_get_order_status_returns_status_field():
    mock = _mock_client("get", json_body={"order": {"id": 1, "status": "READY_TO_SHIP"}})
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        result = await get_order_status("YM-123", 148807227, "tok")
    assert result == "READY_TO_SHIP"


@pytest.mark.asyncio
async def test_get_order_status_raises_on_http_error():
    mock = _mock_client("get", status_code=404)
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        with pytest.raises(httpx.HTTPStatusError):
            await get_order_status("YM-123", 148807227, "tok")
