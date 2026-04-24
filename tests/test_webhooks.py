import pytest
from httpx import AsyncClient, ASGITransport


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
    payload = {
        "type": "ORDER_STATUS_CHANGED",
        "orderId": "YM-123456",
        "status": "PROCESSING",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/market", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == "YM-123456"
