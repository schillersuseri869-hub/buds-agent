import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport

from app.models.events_log import EventLog


def _mock_db(order=None):
    mock = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=order)
    result.scalar_one = MagicMock(return_value=order)
    mock.execute = AsyncMock(return_value=result)
    mock.flush = AsyncMock()
    mock.add = MagicMock()
    mock.commit = AsyncMock()
    mock.refresh = AsyncMock()
    mock.rollback = AsyncMock()
    return mock


@pytest.mark.asyncio
async def test_health_endpoint():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_market_webhook_accepted():
    from app.main import app
    from app.database import get_db

    mock = _mock_db(order=None)

    async def override():
        yield mock

    app.dependency_overrides[get_db] = override
    try:
        payload = {"type": "ORDER_STATUS_CHANGED", "orderId": "YM-123456", "status": "PROCESSING"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhooks/market", json=payload)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert "order_id" in data
    assert data["order_id"]


@pytest.mark.asyncio
async def test_webhook_cancelled_status_publishes_order_cancelled():
    from app.main import app
    from app.database import get_db

    existing_order = MagicMock()
    existing_order.id = uuid.uuid4()
    existing_order.status = "waiting"

    mock = _mock_db(order=existing_order)
    mock_bus = AsyncMock()

    async def override():
        yield mock

    app.dependency_overrides[get_db] = override
    app.state.event_bus = mock_bus
    try:
        payload = {"type": "ORDER_STATUS_CHANGED", "orderId": "YM-CANCEL-001", "status": "CANCELLED"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhooks/market", json=payload)
    finally:
        app.dependency_overrides.pop(get_db, None)
        try:
            del app.state.event_bus
        except AttributeError:
            pass

    assert response.status_code == 200
    mock_bus.publish.assert_awaited_once()
    assert mock_bus.publish.call_args[0][0] == "order.cancelled"


@pytest.mark.asyncio
async def test_webhook_logs_all_payloads_to_events_log():
    from app.main import app
    from app.database import get_db

    mock = _mock_db(order=None)

    async def override():
        yield mock

    app.dependency_overrides[get_db] = override
    try:
        payload = {"type": "ORDER_STATUS_CHANGED", "orderId": "YM-LOG-001", "status": "PROCESSING"}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhooks/market", json=payload)
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    added = [c.args[0] for c in mock.add.call_args_list]
    logs = [o for o in added if isinstance(o, EventLog)]
    assert len(logs) == 1
    assert logs[0].event_type == "market_webhook"
    assert logs[0].payload["orderId"] == "YM-LOG-001"
