import httpx


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
    url = (
        f"https://api.partner.market.yandex.ru"
        f"/campaigns/{campaign_id}/orders/{market_order_id}/delivery/labels"
    )
    return await _fetch_label_bytes(url, api_token)
