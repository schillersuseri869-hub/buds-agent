from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.orders import Order

router = APIRouter()


@router.post("/market")
async def market_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    market_order_id = str(payload.get("orderId", "")).strip()

    if not market_order_id:
        return {"status": "ignored", "reason": "no orderId"}

    result = await db.execute(
        select(Order).where(Order.market_order_id == market_order_id)
    )
    order = result.scalar_one_or_none()

    if order is None:
        try:
            order = Order(market_order_id=market_order_id, status="waiting")
            db.add(order)
            await db.commit()
            await db.refresh(order)
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(Order).where(Order.market_order_id == market_order_id)
            )
            order = result.scalar_one()
            return {"status": "ok", "order_id": str(order.id)}

        bus = getattr(request.app.state, "event_bus", None)
        if bus is not None:
            await bus.publish(
                "order.created",
                {
                    "order_id": str(order.id),
                    "market_order_id": market_order_id,
                },
            )

    return {"status": "ok", "order_id": str(order.id)}
