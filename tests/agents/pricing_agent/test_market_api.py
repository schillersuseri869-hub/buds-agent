import pytest
import csv
import io
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.agents.pricing_agent.market_api import (
    generate_prices_report,
    get_report_status,
    download_and_parse_report,
    fetch_storefront_prices,
    get_promos,
    get_promo_offers,
    update_catalog_prices,
    update_promo_offers,
    ReportTimeoutError,
    ReportGenerationError,
)

_TOKEN = "test_token"
_BIZ_ID = 187548892


@pytest.mark.asyncio
async def test_generate_prices_report_returns_report_id():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"result": {"reportId": "rpt-123"}}

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        result = await generate_prices_report(_BIZ_ID, _TOKEN)

    assert result == "rpt-123"


@pytest.mark.asyncio
async def test_get_report_status_done():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "result": {"status": "DONE", "file": "https://example.com/report.csv"}
    }

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        result = await get_report_status("rpt-123", _TOKEN)

    assert result["status"] == "DONE"
    assert result["file"] == "https://example.com/report.csv"


@pytest.mark.asyncio
async def test_download_and_parse_report_returns_prices():
    csv_content = "offerId\tstorefrontPrice\n" \
                  "SKU-001\t1200.00\n" \
                  "SKU-002\t850.50\n"

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = csv_content

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        result = await download_and_parse_report("https://example.com/report.csv", _TOKEN)

    assert result["SKU-001"] == Decimal("1200.00")
    assert result["SKU-002"] == Decimal("850.50")


@pytest.mark.asyncio
async def test_fetch_storefront_prices_timeout_raises():
    with patch("app.agents.pricing_agent.market_api.generate_prices_report",
               AsyncMock(return_value="rpt-999")), \
         patch("app.agents.pricing_agent.market_api.get_report_status",
               AsyncMock(return_value={"status": "PROCESSING"})), \
         patch("asyncio.sleep", AsyncMock()):
        with pytest.raises(ReportTimeoutError):
            await fetch_storefront_prices(_BIZ_ID, _TOKEN, max_attempts=2, poll_interval=0)


@pytest.mark.asyncio
async def test_update_catalog_prices_sends_batch():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"status": "OK"}

    updates = [
        {"sku": "SKU-001", "value": Decimal("1500"), "discount_base": Decimal("2100"),
         "minimum_for_bestseller": Decimal("1000")},
    ]

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        await update_catalog_prices(_BIZ_ID, _TOKEN, updates)

    MockClient.return_value.post.assert_awaited_once()
    call_kwargs = MockClient.return_value.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs.args[1]
    assert len(payload["offers"]) == 1
    assert payload["offers"][0]["id"] == "SKU-001"
