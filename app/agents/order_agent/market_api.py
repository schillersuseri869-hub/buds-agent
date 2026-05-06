import logging
from datetime import datetime, timezone, timedelta

import httpx

logger = logging.getLogger(__name__)
_BASE = "https://api.partner.market.yandex.ru"
_MSK = timezone(timedelta(hours=3))


async def set_order_ready(market_order_id: str, campaign_id: int, token: str) -> None:
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}/status"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"order": {"status": "PROCESSING", "substatus": "READY_TO_SHIP"}},
            timeout=30.0,
        )
        if not response.is_success:
            logger.error(
                "set_order_ready HTTP %s order=%s body=%s",
                response.status_code, market_order_id, response.text[:500],
            )
        response.raise_for_status()


async def get_order_status(market_order_id: str, campaign_id: int, token: str) -> str:
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    order = data["order"]
    status = order.get("status", "")
    substatus = order.get("substatus", "")
    # FBS: READY_TO_SHIP is a substatus of PROCESSING, not a top-level status
    if status == "PROCESSING" and substatus == "READY_TO_SHIP":
        return "READY_TO_SHIP"
    return status


async def get_order_data(
    market_order_id: str, campaign_id: int, token: str
) -> tuple[list[dict], datetime | None]:
    """Return (items, shipment_deadline_utc) from a single API call."""
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    order = data.get("order", {})

    items = [
        {
            "sku": item.get("offerId", ""),
            "count": item.get("count", 1),
            "price": item.get("prices", {}).get("buyerPrice", 0),
        }
        for item in order.get("items", [])
    ]

    deadline: datetime | None = None
    try:
        shipments = order.get("delivery", {}).get("shipments", [])
        if shipments:
            s = shipments[0]
            date_str = s.get("shipmentDate", "")  # "DD-MM-YYYY"
            time_str = s.get("shipmentTime", "")   # "HH:MM"
            if date_str and time_str:
                naive = datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M")
                deadline = naive.replace(tzinfo=_MSK).astimezone(timezone.utc)
    except Exception as exc:
        logger.warning("Failed to parse shipment deadline for order %s: %s", market_order_id, exc)

    return items, deadline
