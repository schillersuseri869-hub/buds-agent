import base64
import uuid
import fakeredis.aioredis
import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.print_agent.agent import (
    create_print_job,
    download_label,
    get_pending_jobs,
    update_job_status,
    PrintAgent,
)
from app.models.orders import Order
from app.models.print_jobs import PrintJob


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
async def test_download_label_with_format():
    fake_pdf = b"%PDF-1.4 fake"
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=fake_pdf,
    ) as mock_fetch:
        result = await download_label("YM-123", 148807227, "test_token", format="A9_HORIZONTALLY")
    assert result == fake_pdf
    mock_fetch.assert_awaited_once_with(
        "https://api.partner.market.yandex.ru/campaigns/148807227/orders/YM-123/delivery/labels?format=A9_HORIZONTALLY",
        "test_token",
    )


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


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis()


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.market_campaign_id = 148807227
    s.market_api_token = "test_token"
    s.owner_telegram_id = 123456789
    return s


@pytest.fixture
def mock_bot():
    return AsyncMock()


@pytest.mark.asyncio
async def test_handle_order_created_creates_job_and_sends(
    fake_redis, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    # Need an Order in DB first since PrintJob.order_id is FK
    from sqlalchemy.ext.asyncio import AsyncSession
    async with db_factory() as db:
        order = Order(market_order_id="YM-SEND-001", status="waiting")
        db.add(order)
        await db.commit()
        await db.refresh(order)

    fake_pdf = b"%PDF-1.4 fakedata"
    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=fake_pdf,
    ), patch(
        "app.agents.print_agent.agent.send_print_job",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_send:
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-SEND-001"},
        )

    mock_send.assert_awaited_once()
    call_args = mock_send.call_args
    assert base64.b64decode(call_args[0][1]) == fake_pdf

    async with db_factory() as db:
        result = await db.execute(select(PrintJob).where(PrintJob.order_id == order.id))
        jobs = result.scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == "sent"


@pytest.mark.asyncio
async def test_handle_order_created_alerts_when_printer_offline(
    fake_redis, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    async with db_factory() as db:
        order = Order(market_order_id="YM-OFFLINE-001", status="waiting")
        db.add(order)
        await db.commit()
        await db.refresh(order)

    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        return_value=b"%PDF-1.4 x",
    ), patch(
        "app.agents.print_agent.agent.send_print_job",
        new_callable=AsyncMock,
        return_value=False,
    ):
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-OFFLINE-001"},
        )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "офлайн" in text.lower()


@pytest.mark.asyncio
async def test_handle_order_created_alerts_on_api_error(
    fake_redis, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    async with db_factory() as db:
        order = Order(market_order_id="YM-ERR-001", status="waiting")
        db.add(order)
        await db.commit()
        await db.refresh(order)

    with patch(
        "app.agents.print_agent.agent._fetch_label_bytes",
        new_callable=AsyncMock,
        side_effect=httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        ),
    ):
        await agent.handle_order_created(
            "order.created",
            {"order_id": str(order.id), "market_order_id": "YM-ERR-001"},
        )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "ярлык" in text.lower()


@pytest.mark.asyncio
async def test_flush_pending_sends_all_pending_jobs(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-FLUSH-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    fake_pdf = b"%PDF-1.4 flush"
    job1 = await create_print_job(db_session, order.id, "print:pdf:job-flush-1")
    job2 = await create_print_job(db_session, order.id, "print:pdf:job-flush-2")
    await fake_redis.setex("print:pdf:job-flush-1", 86400, fake_pdf)
    await fake_redis.setex("print:pdf:job-flush-2", 86400, fake_pdf)

    sent_ids = []

    async def mock_send(job_id, pdf_b64):
        sent_ids.append(job_id)
        return True

    with patch("app.agents.print_agent.agent.send_print_job", side_effect=mock_send):
        await agent.flush_pending_jobs()

    assert str(job1.id) in sent_ids
    assert str(job2.id) in sent_ids

    async with db_factory() as db:
        result = await db.execute(
            select(PrintJob).where(PrintJob.id.in_([job1.id, job2.id]))
        )
        jobs = result.scalars().all()
    assert all(j.status == "sent" for j in jobs)


@pytest.mark.asyncio
async def test_flush_skips_expired_pdf(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-EXPIRE-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    await create_print_job(db_session, order.id, "print:pdf:expired-key")
    # do NOT put anything in Redis — simulates TTL expiry

    with patch(
        "app.agents.print_agent.agent.send_print_job", new_callable=AsyncMock
    ) as mock_send:
        await agent.flush_pending_jobs()

    mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_ack_done_updates_status(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-ACK-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "print:pdf:ack-1")
    await update_job_status(db_session, job.id, "sent")

    await agent.handle_ack({"job_id": str(job.id), "status": "done"})

    async with db_factory() as db:
        result = await db.execute(select(PrintJob).where(PrintJob.id == job.id))
        updated = result.scalar_one()
    assert updated.status == "done"
    assert updated.completed_at is not None


@pytest.mark.asyncio
async def test_handle_ack_failed_alerts_owner(
    fake_redis, db_session, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    order = Order(market_order_id="YM-ACK-FAIL-001", status="waiting")
    db_session.add(order)
    await db_session.commit()
    await db_session.refresh(order)

    job = await create_print_job(db_session, order.id, "print:pdf:ack-fail-1")
    await update_job_status(db_session, job.id, "sent")

    await agent.handle_ack(
        {"job_id": str(job.id), "status": "failed", "error": "paper jam"}
    )

    mock_bot.send_message.assert_awaited_once()
    text = mock_bot.send_message.call_args[0][1]
    assert "ошибка" in text.lower()
    assert "paper jam" in text.lower()


@pytest.mark.asyncio
async def test_handle_ack_unknown_job_id_is_ignored(
    fake_redis, mock_bot, mock_settings, test_engine
):
    db_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    agent = PrintAgent(fake_redis, db_factory, mock_bot, mock_settings)

    # Send an ACK for a UUID that doesn't exist in DB
    await agent.handle_ack({"job_id": str(uuid.UUID(int=0)), "status": "done"})

    mock_bot.send_message.assert_not_awaited()
