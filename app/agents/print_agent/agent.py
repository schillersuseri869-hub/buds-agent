"""
Print Agent — VPS side.

Responsibilities: label download, PrintJob DB operations, PrintAgent class.
"""
import base64
import logging
import uuid as uuid_module
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from redis.asyncio import Redis
from aiogram import Bot

from app.models.print_jobs import PrintJob
from app.api.ws_print import send_print_job

logger = logging.getLogger(__name__)

_VALID_STATUSES = frozenset({"pending", "sent", "done", "failed"})


async def _fetch_label_bytes(url: str, token: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=True,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.content


async def download_label(
    market_order_id: str, campaign_id: int, api_token: str, format: str | None = None
) -> bytes:
    if not market_order_id or "/" in market_order_id:
        raise ValueError(f"Invalid market_order_id: {market_order_id!r}")
    url = (
        f"https://api.partner.market.yandex.ru"
        f"/campaigns/{campaign_id}/orders/{market_order_id}/delivery/labels"
    )
    if format:
        url += f"?format={format}"
    return await _fetch_label_bytes(url, api_token)


async def create_print_job(
    db: AsyncSession, order_id: uuid_module.UUID, redis_key: str
) -> PrintJob:
    if not redis_key:
        raise ValueError("redis_key must not be empty")
    job = PrintJob(order_id=order_id, status="pending", label_url=redis_key)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_pending_jobs(db: AsyncSession) -> list[PrintJob]:
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.status.in_(["pending", "sent"]))
        .order_by(PrintJob.created_at, PrintJob.id)
    )
    return list(result.scalars().all())


async def update_job_status(
    db: AsyncSession, job_id: uuid_module.UUID, status: str
) -> PrintJob | None:
    if status not in _VALID_STATUSES:
        raise ValueError(f"Invalid status: {status!r}")
    result = await db.execute(select(PrintJob).where(PrintJob.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        return None
    job.status = status
    if status in ("done", "failed"):
        job.completed_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(job)
    return job


class PrintAgent:
    def __init__(
        self,
        redis: Redis,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
    ):
        self._redis = redis
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings

    async def handle_order_created(self, channel: str, data: dict) -> None:
        market_order_id = data.get("market_order_id")
        order_id_str = data.get("order_id")
        if not market_order_id or not order_id_str:
            logger.error("order.created missing fields: %s", data)
            return

        try:
            pdf_bytes = await download_label(
                market_order_id,
                self._settings.market_campaign_id,
                self._settings.market_api_token,
                format="A9_HORIZONTALLY",
            )
        except Exception as exc:
            logger.error("Label download failed for %s: %s", market_order_id, exc)
            await self._alert(f"Не удалось скачать ярлык заказа #{market_order_id}")
            return

        job_id = str(uuid_module.uuid4())
        redis_key = f"print:pdf:{job_id}"
        await self._redis.setex(redis_key, 86400, pdf_bytes)

        try:
            order_uuid = uuid_module.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID in order.created: %s", order_id_str)
            return

        async with self._db_factory() as db:
            job = await create_print_job(
                db, order_uuid, redis_key
            )

        pdf_b64 = base64.b64encode(pdf_bytes).decode()
        sent = await send_print_job(str(job.id), pdf_b64)

        if sent:
            async with self._db_factory() as db:
                await update_job_status(db, job.id, "sent")
        else:
            await self._alert(
                f"Принтер офлайн. Ярлык заказа #{market_order_id} "
                f"будет напечатан при подключении."
            )

    async def flush_pending_jobs(self) -> None:
        async with self._db_factory() as db:
            jobs = await get_pending_jobs(db)

        for job in jobs:
            pdf_bytes = await self._redis.get(job.label_url)
            if pdf_bytes is None:
                logger.warning("PDF expired in Redis for job %s, skipping", job.id)
                continue
            pdf_b64 = base64.b64encode(pdf_bytes).decode()
            sent = await send_print_job(str(job.id), pdf_b64)
            if sent and job.status != "sent":
                async with self._db_factory() as db:
                    await update_job_status(db, job.id, "sent")

    async def handle_ack(self, ack: dict) -> None:
        job_id_str = ack.get("job_id")
        status = ack.get("status")
        if not job_id_str or status not in ("done", "failed"):
            return
        try:
            job_id = uuid_module.UUID(job_id_str)
        except ValueError:
            logger.warning("Invalid job_id in ACK: %s", job_id_str)
            return

        async with self._db_factory() as db:
            updated = await update_job_status(db, job_id, status)
        if updated is None:
            logger.warning("handle_ack: job %s not found in DB", job_id_str)
            return

        if status == "failed":
            error = ack.get("error", "неизвестная ошибка")
            await self._alert(f"Ошибка печати задания {job_id_str}: {error}")

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, message
            )
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)
