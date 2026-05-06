import logging

import httpx

logger = logging.getLogger(__name__)
_BASE = "https://api.partner.market.yandex.ru"


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


async def get_order_items(market_order_id: str, campaign_id: int, token: str) -> list[dict]:
    """Return [{sku, name, count}] for the order."""
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
    logger.info("order delivery object for %s: %s", market_order_id, order.get("delivery"))
    return [
        {
            "sku": item.get("offerId", ""),
            "count": item.get("count", 1),
            "price": item.get("prices", {}).get("buyerPrice", 0),
        }
        for item in order.get("items", [])
    ]
