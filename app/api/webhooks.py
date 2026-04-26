from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.orders import Order
from app.models.events_log import EventLog

router = APIRouter()

_MARKET_STATUS_MAP = {
    "PROCESSING": ("order.created", "waiting"),
    "READY_TO_SHIP": ("order.ready", "ready"),
    "SHIPPED": ("order.shipped", "shipped"),
    "DELIVERED": ("order.delivered", "delivered"),
    "CANCELLED": ("order.cancelled", "cancelled"),
    "CANCELLED_IN_DELIVERY": ("order.cancelled", "cancelled"),
}


@router.post("/market")
async def market_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    market_order_id = str(payload.get("orderId", "")).strip()
    market_status = str(payload.get("status", "PROCESSING")).strip()

    if not market_order_id:
        return {"status": "ignored", "reason": "no orderId"}

    log_entry = EventLog(event_type="market_webhook", payload=payload)
    db.add(log_entry)
    await db.flush()

    bus = getattr(request.app.state, "event_bus", None)
    event_name, db_status = _MARKET_STATUS_MAP.get(market_status, ("order.created", "waiting"))

    result = await db.execute(
        select(Order).where(Order.market_order_id == market_order_id)
    )
    order = result.scalar_one_or_none()

    if order is None:
        if event_name != "order.created":
            await db.commit()
            return {"status": "ignored", "reason": "order not found"}
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

        if bus is not None:
            await bus.publish("order.created", {
                "order_id": str(order.id),
                "market_order_id": market_order_id,
            })
    else:
        if event_name != "order.created" and order.status != db_status:
            order.status = db_status
            await db.commit()
            if bus is not None:
                await bus.publish(event_name, {
                    "order_id": str(order.id),
                    "market_order_id": market_order_id,
                })
        else:
            await db.commit()

    return {"status": "ok", "order_id": str(order.id)}
