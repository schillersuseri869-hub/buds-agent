import uuid
from unittest.mock import patch, AsyncMock

import httpx
import pytest

from app.agents.print_agent.agent import (
    create_print_job,
    download_label,
    get_pending_jobs,
    update_job_status,
)
from app.models.orders import Order


@pytest.mark.asyncio
async def test_download_label_returns_bytes():
    fake_pdf = b"%PDF-1.4 fake"
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=fake_pdf,
    ) as mock_fetch:
        result = await download_label("YM-123", 148807227, "test_token")
    assert result == fake_pdf
    mock_fetch.assert_awaited_once_with(
        "https://api.partner.market.yandex.ru/campaigns/148807227/orders/YM-123/delivery/labels",
        "test_token",
    )


@pytest.mark.asyncio
async def test_download_label_raises_on_http_error():
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "http://test"),
            response=httpx.Response(404),
        ),
    ):
        with pytest.raises(httpx.HTTPStatusError):
            await download_label("UNKNOWN", 148807227, "test_token")


@pytest.mark.asyncio
async def test_download_label_rejects_invalid_order_id():
    with pytest.raises(ValueError):
        await download_label("", 148807227, "test_token")

    with pytest.raises(ValueError):
        await download_label("YM/123", 148807227, "test_token")


@pytest.mark.asyncio
async def test_create_print_job(db_session):
    order = Order(market_order_id="YM-TEST-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "redis:print:pdf:abc123")

    assert job.id is not None
    assert job.status == "pending"
    assert job.label_url == "redis:print:pdf:abc123"
    assert job.order_id == order.id
    assert job.completed_at is None


@pytest.mark.asyncio
async def test_get_pending_jobs_returns_pending_and_sent(db_session):
    order = Order(market_order_id="YM-TEST-002", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job_pending = await create_print_job(db_session, order.id, "redis:print:pdf:p1")
    job_sent = await create_print_job(db_session, order.id, "redis:print:pdf:p2")
    await update_job_status(db_session, job_sent.id, "sent")
    job_done = await create_print_job(db_session, order.id, "redis:print:pdf:p3")
    await update_job_status(db_session, job_done.id, "done")

    jobs = await get_pending_jobs(db_session)
    ids = {j.id for j in jobs}
    assert job_pending.id in ids
    assert job_sent.id in ids
    assert job_done.id not in ids


@pytest.mark.asyncio
async def test_update_job_status_done_sets_completed_at(db_session):
    order = Order(market_order_id="YM-TEST-003", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "redis:print:pdf:x1")
    updated = await update_job_status(db_session, job.id, "done")

    assert updated.status == "done"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_update_job_status_unknown_id_returns_none(db_session):
    result = await update_job_status(db_session, uuid.uuid4(), "done")
    assert result is None


@pytest.mark.asyncio
async def test_update_job_status_failed_sets_completed_at(db_session):
    order = Order(market_order_id="YM-TEST-004", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "redis:print:pdf:x2")
    updated = await update_job_status(db_session, job.id, "failed")

    assert updated.status == "failed"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_update_job_status_invalid_status_raises():
    with pytest.raises(ValueError, match="Invalid status"):
        await update_job_status(None, uuid.uuid4(), "printing")
