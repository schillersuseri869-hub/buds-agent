from datetime import datetime, timezone

import httpx

_BASE = "https://api.partner.market.yandex.ru"


async def get_order_items(
    market_order_id: str, campaign_id: int, token: str
) -> list[dict]:
    """Return [{sku, count, price}] for the given order."""
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    return [
        {
            "sku": item["offerId"],
            "count": item["count"],
            "price": item.get("prices", {}).get("buyerPrice", 0),
        }
        for item in data.get("order", {}).get("items", [])
    ]


async def update_stocks(
    campaign_id: int,
    token: str,
    warehouse_id: int,
    skus: dict[str, int],
) -> None:
    """Batch-update stock availability on Yandex Market. skus: {market_sku → count}"""
    if not skus:
        return
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "skus": [
            {
                "sku": sku,
                "warehouseId": warehouse_id,
                "items": [{"type": "FIT", "count": max(0, count), "updatedAt": now}],
            }
            for sku, count in skus.items()
        ]
    }
    url = f"{_BASE}/campaigns/{campaign_id}/offers/stocks"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
            timeout=60.0,
        )
        response.raise_for_status()
