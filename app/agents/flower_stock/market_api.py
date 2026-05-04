from datetime import datetime, timezone

import httpx

_BASE = "https://api.partner.market.yandex.ru"


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
