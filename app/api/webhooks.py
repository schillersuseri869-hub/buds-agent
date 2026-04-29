from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.orders import Order
from app.models.events_log import EventLog

router = APIRouter()

# notificationType → (event_bus_name, db_status)
_TYPE_MAP = {
    "ORDER_CREATED": ("order.created", "waiting"),
    "ORDER_CANCELLED": ("order.cancelled", "cancelled"),
}

# For ORDER_STATUS_UPDATED: map status field value → (event_bus_name, db_status)
_STATUS_MAP = {
    "PROCESSING": None,  # depends on substatus
    "DELIVERY": ("order.shipped", "shipped"),
    "DELIVERED": ("order.delivered", "delivered"),
    "CANCELLED": ("order.cancelled", "cancelled"),
}

_PING_RESPONSE = {
    "name": "BUDS",
    "time": datetime.now(timezone.utc).isoformat(),
    "version": "1.0",
}


@router.post("/market/notification")
@router.post("/market")
async def market_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    notification_type = payload.get("notificationType", "")

    if notification_type == "PING":
        return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}

    market_order_id = str(payload.get("orderId", "")).strip()
    if not market_order_id:
        return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}

    log_entry = EventLog(event_type="market_webhook", payload=payload)
    db.add(log_entry)
    await db.flush()

    bus = getattr(request.app.state, "event_bus", None)

    if notification_type == "ORDER_STATUS_UPDATED":
        status = payload.get("status", "")
        substatus = payload.get("substatus", "")
        if status == "PROCESSING" and substatus == "READY_TO_SHIP":
            event_name, db_status = "order.ready", "ready"
        elif status in _STATUS_MAP and _STATUS_MAP[status] is not None:
            event_name, db_status = _STATUS_MAP[status]
        else:
            await db.commit()
            return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}
    elif notification_type in _TYPE_MAP:
        event_name, db_status = _TYPE_MAP[notification_type]
    else:
        await db.commit()
        return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}

    result = await db.execute(select(Order).where(Order.market_order_id == market_order_id))
    order = result.scalar_one_or_none()

    if order is None:
        if event_name != "order.created":
            await db.commit()
            return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}
        try:
            order = Order(market_order_id=market_order_id, status="waiting")
            db.add(order)
            await db.commit()
            await db.refresh(order)
        except IntegrityError:
            await db.rollback()
            result = await db.execute(select(Order).where(Order.market_order_id == market_order_id))
            order = result.scalar_one()
            return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}

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

    return {"name": "BUDS", "time": datetime.now(timezone.utc).isoformat(), "version": "1.0"}
