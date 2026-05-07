import asyncio
import csv
import io
import logging
import zipfile
from dataclasses import dataclass, field
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


@dataclass
class PricesReport:
    storefront: dict[str, Decimal] = field(default_factory=dict)
    catalog: dict[str, Decimal] = field(default_factory=dict)
    crossed: dict[str, Decimal] = field(default_factory=dict)


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def generate_prices_report(business_id: int, token: str) -> str:
    url = f"{_BASE}/v2/reports/goods-prices/generate?format=CSV"
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


def _parse_decimal(raw: str) -> Decimal | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return Decimal(raw.replace(",", "."))
    except InvalidOperation:
        return None


async def download_and_parse_report(file_url: str, token: str) -> PricesReport:
    """Download ZIP(CSV) report and return PricesReport with storefront, catalog, crossed prices."""
    async with httpx.AsyncClient() as client:
        response = await client.get(file_url, headers=_headers(token), timeout=60.0)
        response.raise_for_status()
        content = response.content

    result = PricesReport()

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next((n for n in zf.namelist() if n.endswith(".csv")), None)
        if csv_name is None:
            logger.error("No CSV file found in prices report ZIP: %s", zf.namelist())
            return result
        raw = zf.read(csv_name)

    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        logger.error("Cannot decode prices report CSV")
        return result

    reader = csv.DictReader(io.StringIO(text, newline=""))
    for row in reader:
        sku = row.get("OFFER_ID", "").strip()
        if not sku:
            continue
        if (v := _parse_decimal(row.get("ON_DISPLAY", ""))) is not None:
            result.storefront[sku] = v
        if (v := _parse_decimal(row.get("BASIC_PRICE", ""))) is not None:
            result.catalog[sku] = v
        if (v := _parse_decimal(row.get("BASIC_DISCOUNT_BASE", ""))) is not None:
            result.crossed[sku] = v

    logger.info(
        "Prices report parsed: storefront=%d catalog=%d crossed=%d SKUs",
        len(result.storefront), len(result.catalog), len(result.crossed),
    )
    return result


async def fetch_prices_report(
    business_id: int,
    token: str,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
) -> PricesReport:
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
    def _build_offer(u: dict) -> dict:
        value = float(u["value"])
        discount_base = u.get("discount_base") or 0
        min_bs = u.get("minimum_for_bestseller") or 0

        price: dict = {"value": value, "currencyId": "RUR"}
        if discount_base and float(discount_base) > value:
            price["discountBase"] = float(discount_base)

        offer: dict = {"offerId": u["sku"], "price": price}
        if min_bs and float(min_bs) > 0:
            offer["minimumForBestseller"] = {"value": float(min_bs), "currencyId": "RUR"}
        return offer

    payload = {"offers": [_build_offer(u) for u in updates]}
    logger.debug("update_catalog_prices payload: %s", payload)
    url = f"{_BASE}/v2/businesses/{business_id}/offer-prices/updates"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url, headers=_headers(token), json=payload, timeout=60.0
        )
        if not response.is_success:
            logger.error("update_catalog_prices %s: %s", response.status_code, response.text[:1000])
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
