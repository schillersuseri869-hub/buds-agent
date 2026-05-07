import io
import csv
import zipfile
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.pricing_agent.market_api import (
    generate_prices_report,
    get_report_status,
    download_and_parse_report,
    fetch_prices_report,
    get_promos,
    get_promo_offers,
    update_catalog_prices,
    update_promo_offers,
    ReportTimeoutError,
    ReportGenerationError,
    PricesReport,
)

_TOKEN = "test_token"
_BIZ_ID = 187548892


def _make_zip_csv(rows: list[dict]) -> bytes:
    """Build a ZIP containing a CSV with the Yandex report column names."""
    fieldnames = ["OFFER_ID", "ON_DISPLAY", "BASIC_PRICE", "BASIC_DISCOUNT_BASE"]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    csv_bytes = buf.getvalue().encode("utf-8")

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("report.csv", csv_bytes)
    return zip_buf.getvalue()


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
        "result": {"status": "DONE", "file": "https://example.com/report.zip"}
    }

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        result = await get_report_status("rpt-123", _TOKEN)

    assert result["status"] == "DONE"


@pytest.mark.asyncio
async def test_download_and_parse_report_returns_prices_report():
    zip_content = _make_zip_csv([
        {"OFFER_ID": "SKU-001", "ON_DISPLAY": "1200.00", "BASIC_PRICE": "1500.00", "BASIC_DISCOUNT_BASE": "2100.00"},
        {"OFFER_ID": "SKU-002", "ON_DISPLAY": "850,50",  "BASIC_PRICE": "900.00",  "BASIC_DISCOUNT_BASE": ""},
    ])

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.content = zip_content

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_response)
        result = await download_and_parse_report("https://example.com/report.zip", _TOKEN)

    assert isinstance(result, PricesReport)
    assert result.storefront["SKU-001"] == Decimal("1200.00")
    assert result.storefront["SKU-002"] == Decimal("850.50")
    assert result.catalog["SKU-001"] == Decimal("1500.00")
    assert "SKU-002" not in result.crossed  # empty BASIC_DISCOUNT_BASE → not stored


@pytest.mark.asyncio
async def test_fetch_prices_report_timeout_raises():
    with patch("app.agents.pricing_agent.market_api.generate_prices_report",
               AsyncMock(return_value="rpt-999")), \
         patch("app.agents.pricing_agent.market_api.get_report_status",
               AsyncMock(return_value={"status": "PROCESSING"})), \
         patch("asyncio.sleep", AsyncMock()):
        with pytest.raises(ReportTimeoutError):
            await fetch_prices_report(_BIZ_ID, _TOKEN, max_attempts=2, poll_interval=0)


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
    offer = payload["offers"][0]
    assert offer["offerId"] == "SKU-001"
    assert offer["price"]["discountBase"] == 2100.0
    assert offer["minimumForBestseller"]["value"] == 1000.0


@pytest.mark.asyncio
async def test_update_catalog_prices_omits_invalid_discount_base():
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"status": "OK"}

    updates = [
        {"sku": "SKU-002", "value": Decimal("1500"), "discount_base": Decimal("0"),
         "minimum_for_bestseller": Decimal("1000")},
        {"sku": "SKU-003", "value": Decimal("1500"), "discount_base": Decimal("1200"),
         "minimum_for_bestseller": Decimal("0")},
    ]

    with patch("httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_response)
        await update_catalog_prices(_BIZ_ID, _TOKEN, updates)

    payload = MockClient.return_value.post.call_args.kwargs.get("json")
    # SKU-002: discount_base=0 → no discountBase; min_bs=1000 → included
    assert "discountBase" not in payload["offers"][0]["price"]
    assert "minimumForBestseller" in payload["offers"][0]
    # SKU-003: discount_base=1200 < value=1500 → no discountBase; min_bs=0 → omitted
    assert "discountBase" not in payload["offers"][1]["price"]
    assert "minimumForBestseller" not in payload["offers"][1]
