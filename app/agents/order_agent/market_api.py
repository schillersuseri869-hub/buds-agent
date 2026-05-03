import httpx

_BASE = "https://api.partner.market.yandex.ru"


async def set_order_ready(market_order_id: str, campaign_id: int, token: str) -> None:
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}/status"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"order": {"status": "READY_TO_SHIP"}},
            timeout=30.0,
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
    return data["order"]["status"]


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
    return [
        {
            "sku": item.get("offerId", ""),
            "name": item.get("offerName", ""),
            "count": item.get("count", 1),
        }
        for item in data.get("order", {}).get("items", [])
    ]
