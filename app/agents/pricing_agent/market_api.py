import asyncio
import csv
import io
import logging
from decimal import Decimal, InvalidOperation

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://api.partner.market.yandex.ru"
_DEFAULT_POLL_INTERVAL = 30
_DEFAULT_MAX_ATTEMPTS = 10  # 10 × 30s = 5 min


class ReportGenerationError(Exception):
    pass


class ReportTimeoutError(Exception):
    pass


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def generate_prices_report(business_id: int, token: str) -> str:
    url = f"{_BASE}/v2/reports/goods-prices/generate"
    payload = {"businessId": business_id}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=30.0
        )
        response.raise_for_status()
        return response.json()["result"]["reportId"]


async def get_report_status(report_id: str, token: str) -> dict:
    url = f"{_BASE}/v2/reports/info/{report_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=_headers(token), timeout=30.0)
        response.raise_for_status()
        return response.json()["result"]


async def download_and_parse_report(file_url: str, token: str) -> dict[str, Decimal]:
    """Download TSV/CSV report and return {market_sku: storefront_price}."""
    async with httpx.AsyncClient() as client:
        response = await client.get(file_url, headers=_headers(token), timeout=60.0)
        response.raise_for_status()
        text = response.text

    prices: dict[str, Decimal] = {}
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    for row in reader:
        sku = (row.get("offerId") or row.get("sku") or "").strip()
        raw_price = (row.get("storefrontPrice") or row.get("price") or "").strip()
        if not sku or not raw_price:
            continue
        try:
            prices[sku] = Decimal(raw_price.replace(",", "."))
        except InvalidOperation:
            logger.warning("Cannot parse storefront price for %s: %r", sku, raw_price)
    return prices


async def fetch_storefront_prices(
    business_id: int,
    token: str,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
) -> dict[str, Decimal]:
    report_id = await generate_prices_report(business_id, token)
    for _ in range(max_attempts):
        await asyncio.sleep(poll_interval)
        status = await get_report_status(report_id, token)
        if status["status"] == "DONE":
            return await download_and_parse_report(status["file"], token)
        if status["status"] == "FAILED":
            raise ReportGenerationError(f"Report {report_id} failed: {status}")
    raise ReportTimeoutError(f"Report {report_id} did not complete in time")


async def get_promos(business_id: int, token: str) -> list[dict]:
    """Return active and upcoming promos."""
    url = f"{_BASE}/v2/businesses/{business_id}/promos"
    payload = {"statuses": ["ACTIVE", "UPCOMING"]}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=30.0
        )
        response.raise_for_status()
        return response.json().get("promos", [])


async def get_promo_offers(
    business_id: int, token: str, promo_id: str
) -> list[dict]:
    """Return offers currently in a promo: [{offerId, price, ...}]."""
    url = f"{_BASE}/v2/businesses/{business_id}/promos/offers"
    payload = {"promoId": promo_id}
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        return data.get("offers", []) or data.get("result", {}).get("offers", [])


async def update_catalog_prices(
    business_id: int,
    token: str,
    updates: list[dict],
) -> None:
    """
    Batch-update catalog prices.
    updates: list of {sku, value, discount_base, minimum_for_bestseller}
    """
    if not updates:
        return
    payload = {
        "offers": [
            {
                "id": u["sku"],
                "price": {
                    "value": float(u["value"]),
                    "currencyId": "RUR",
                    "discountBase": float(u["discount_base"]),
                },
                "minimumForBestseller": {
                    "value": float(u["minimum_for_bestseller"]),
                    "currencyId": "RUR",
                },
            }
            for u in updates
        ]
    }
    url = f"{_BASE}/v2/businesses/{business_id}/offer-prices/updates"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=60.0
        )
        response.raise_for_status()


async def update_promo_offers(
    business_id: int,
    token: str,
    promo_id: str,
    offers: list[dict],
) -> dict:
    """
    Add/update SKUs in a promo.
    offers: list of {sku, promo_price} (promo_price=None for fixed-discount promos)
    Returns API response dict (may contain rejected offers).
    """
    if not offers:
        return {}
    payload = {
        "promoId": promo_id,
        "offers": [
            {
                "offerId": o["sku"],
                **({"price": {"value": float(o["promo_price"]), "currencyId": "RUR"}}
                   if o.get("promo_price") is not None else {}),
            }
            for o in offers
        ],
    }
    url = f"{_BASE}/v2/businesses/{business_id}/promos/offers/update"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=60.0
        )
        response.raise_for_status()
        return response.json()
