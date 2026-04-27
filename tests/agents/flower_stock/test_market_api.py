import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from app.agents.flower_stock.market_api import get_order_items, update_stocks


@pytest.mark.asyncio
async def test_get_order_items_parses_response():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    fake_response.json.return_value = {
        "order": {
            "items": [
                {"offerId": "SKU-001", "count": 2, "prices": {"buyerPrice": 500}},
                {"offerId": "SKU-002", "count": 1, "prices": {"buyerPrice": 300}},
            ]
        }
    }
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_cls.return_value = mock_client
        result = await get_order_items("ORDER-1", 148807227, "token")

    assert result == [
        {"sku": "SKU-001", "count": 2, "price": 500},
        {"sku": "SKU-002", "count": 1, "price": 300},
    ]


@pytest.mark.asyncio
async def test_get_order_items_raises_on_http_error():
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404",
            request=httpx.Request("GET", "http://x"),
            response=httpx.Response(404),
        ))
        mock_cls.return_value = mock_client
        with pytest.raises(httpx.HTTPStatusError):
            await get_order_items("BAD", 148807227, "token")


@pytest.mark.asyncio
async def test_update_stocks_sends_correct_payload():
    fake_response = MagicMock()
    fake_response.raise_for_status = MagicMock()
    captured = {}

    async def fake_put(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return fake_response

    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = fake_put
        mock_cls.return_value = mock_client
        await update_stocks(148807227, "tok", 99, {"SKU-A": 5, "SKU-B": 0})

    assert captured["url"].endswith("/campaigns/148807227/offers/stocks")
    skus = {s["sku"]: s["items"][0]["count"] for s in captured["json"]["skus"]}
    assert skus == {"SKU-A": 5, "SKU-B": 0}
    assert all(s["warehouseId"] == 99 for s in captured["json"]["skus"])


@pytest.mark.asyncio
async def test_update_stocks_skips_empty_dict():
    with patch("httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.put = AsyncMock()
        mock_cls.return_value = mock_client
        await update_stocks(148807227, "tok", 99, {})
        mock_client.put.assert_not_called()
