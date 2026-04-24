import asyncio
import pytest
import pytest_asyncio
import fakeredis.aioredis
from app.core.event_bus import EventBus


@pytest_asyncio.fixture
async def bus():
    fake = fakeredis.aioredis.FakeRedis()
    b = EventBus(fake)
    yield b
    await b.close()


@pytest.mark.asyncio
async def test_publish_and_subscribe(bus):
    received = []

    async def handler(channel: str, data: dict):
        received.append((channel, data))

    await bus.subscribe("order.created", handler)
    await bus.publish("order.created", {"order_id": "YM-001"})
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0][0] == "order.created"
    assert received[0][1]["order_id"] == "YM-001"


@pytest.mark.asyncio
async def test_multiple_subscribers(bus):
    log_a, log_b = [], []

    await bus.subscribe("order.created", lambda ch, d: log_a.append(d))
    await bus.subscribe("order.created", lambda ch, d: log_b.append(d))
    await bus.publish("order.created", {"order_id": "YM-002"})
    await asyncio.sleep(0.1)

    assert len(log_a) == 1
    assert len(log_b) == 1


@pytest.mark.asyncio
async def test_different_channels_isolated(bus):
    stock_events = []

    await bus.subscribe("stock.updated", lambda ch, d: stock_events.append(d))
    await bus.publish("order.created", {"order_id": "YM-003"})
    await asyncio.sleep(0.1)

    assert stock_events == []
