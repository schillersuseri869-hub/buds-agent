from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/market")
async def market_webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("type", "unknown")
    order_id = payload.get("orderId")
    # TODO(order_agent): route to event_bus.publish("order.created", ...) in Order Agent plan
    return {"received": event_type, "order_id": order_id}
