"""
Print Agent — VPS side.

Responsibilities: label download, PrintJob DB operations, PrintAgent class.
"""
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.print_jobs import PrintJob


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
    market_order_id: str, campaign_id: int, api_token: str
) -> bytes:
    if not market_order_id or "/" in market_order_id:
        raise ValueError(f"Invalid market_order_id: {market_order_id!r}")
    url = (
        f"https://api.partner.market.yandex.ru"
        f"/campaigns/{campaign_id}/orders/{market_order_id}/delivery/labels"
    )
    return await _fetch_label_bytes(url, api_token)


async def create_print_job(
    db: AsyncSession, order_id: uuid.UUID, redis_key: str
) -> PrintJob:
    job = PrintJob(order_id=order_id, status="pending", label_url=redis_key)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return job


async def get_pending_jobs(db: AsyncSession) -> list[PrintJob]:
    result = await db.execute(
        select(PrintJob)
        .where(PrintJob.status.in_(["pending", "sent"]))
        .order_by(PrintJob.created_at)
    )
    return list(result.scalars().all())


async def update_job_status(
    db: AsyncSession, job_id: uuid.UUID, status: str
) -> PrintJob | None:
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
