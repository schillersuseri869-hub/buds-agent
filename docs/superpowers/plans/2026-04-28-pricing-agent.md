# Pricing Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a scheduled agent that runs every 3 hours to sync catalog prices with Yandex Market, monitor storefront prices, and manage promo participation for all ~900 SKUs.

**Architecture:** Single `PricingAgent` class with an APScheduler job orchestrating 6 sequential phases (data collection → catalog sync → storefront monitoring → promo management → DB write → Telegram report). `price_engine.py` is pure logic with no I/O — all Yandex API calls live in `market_api.py`.

**Tech Stack:** Python 3.11+, SQLAlchemy 2.0 async, httpx, APScheduler (already in project), aiogram 3.x, pytest-asyncio + unittest.mock for tests.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `app/models/promo_participations.py` | Create | PromoParticipation ORM model |
| `app/models/market_products.py` | Modify | Add `is_pr` bool column |
| `app/models/price_history.py` | Modify | Add `promo_price` nullable column |
| `app/models/__init__.py` | Modify | Import PromoParticipation |
| `app/agents/pricing_agent/__init__.py` | Create | Empty |
| `app/agents/pricing_agent/market_api.py` | Create | All Yandex API calls for pricing |
| `app/agents/pricing_agent/price_engine.py` | Create | Pure business logic, no I/O |
| `app/agents/pricing_agent/agent.py` | Create | PricingAgent + APScheduler job |
| `app/agents/flower_stock/sheets_loader.py` | Modify | Read column G (`is_pr`) |
| `app/bot/owner_bot.py` | Modify | Quarantine confirm/skip callback |
| `app/main.py` | Modify | Wire PricingAgent into lifespan |
| `tests/agents/pricing_agent/__init__.py` | Create | Empty |
| `tests/agents/pricing_agent/test_market_api.py` | Create | Tests for API layer |
| `tests/agents/pricing_agent/test_price_engine.py` | Create | Tests for business logic |
| `tests/agents/pricing_agent/test_agent.py` | Create | Tests for agent orchestration |

---

## Task 1: DB Model Changes

**Files:**
- Create: `app/models/promo_participations.py`
- Modify: `app/models/market_products.py`
- Modify: `app/models/price_history.py`
- Modify: `app/models/__init__.py`

- [ ] **Step 1: Write failing model tests**

Create `tests/agents/pricing_agent/__init__.py` (empty), then add to `tests/test_models.py`:

```python
import pytest
from decimal import Decimal
from app.models.market_products import MarketProduct
from app.models.price_history import PriceHistory
from app.models.promo_participations import PromoParticipation


@pytest.mark.asyncio
async def test_market_product_has_is_pr(db_session):
    prod = MarketProduct(
        market_sku="TEST-PR-001",
        name="Test PR SKU",
        catalog_price=Decimal("1900"),
        crossed_price=Decimal("2660"),
        min_price=Decimal("1000"),
        optimal_price=Decimal("1000"),
        is_pr=True,
    )
    db_session.add(prod)
    await db_session.commit()
    await db_session.refresh(prod)
    assert prod.is_pr is True


@pytest.mark.asyncio
async def test_price_history_has_promo_price(db_session):
    import uuid
    ph = PriceHistory(
        product_id=uuid.uuid4(),
        catalog_price=Decimal("1500"),
        storefront_price=Decimal("1200"),
        min_price=Decimal("1000"),
        optimal_price=Decimal("1000"),
        promo_price=Decimal("1100"),
    )
    db_session.add(ph)
    await db_session.commit()
    await db_session.refresh(ph)
    assert ph.promo_price == Decimal("1100")


@pytest.mark.asyncio
async def test_promo_participation_create(db_session):
    import uuid
    from datetime import datetime, timezone
    pp = PromoParticipation(
        product_id=uuid.uuid4(),
        promo_id="promo-abc-123",
        promo_type="direct_discount",
        promo_price=Decimal("1100"),
        discount_pct=None,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(pp)
    await db_session.commit()
    await db_session.refresh(pp)
    assert pp.promo_id == "promo-abc-123"
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_models.py::test_market_product_has_is_pr tests/test_models.py::test_price_history_has_promo_price tests/test_models.py::test_promo_participation_create -v
```

Expected: `ImportError` or `TypeError` (model fields missing).

- [ ] **Step 3: Add `is_pr` to `app/models/market_products.py`**

```python
import uuid
from decimal import Decimal
from sqlalchemy import String, Numeric, Enum, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class MarketProduct(Base):
    __tablename__ = "market_products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    market_sku: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    catalog_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    crossed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    optimal_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "hidden", name="product_status"), nullable=False, default="active"
    )
```

- [ ] **Step 4: Add `promo_price` to `app/models/price_history.py`**

```python
import uuid
from decimal import Decimal
from typing import Optional
from datetime import datetime
from sqlalchemy import Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    catalog_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    storefront_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    optimal_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    promo_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
```

- [ ] **Step 5: Create `app/models/promo_participations.py`**

```python
import uuid
from decimal import Decimal
from typing import Optional
from datetime import datetime
from sqlalchemy import String, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PromoParticipation(Base):
    __tablename__ = "promo_participations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    promo_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    promo_type: Mapped[str] = mapped_column(String(50), nullable=False)
    promo_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    discount_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 6: Update `app/models/__init__.py`**

```python
from app.models.base import Base
from app.models.raw_materials import RawMaterial
from app.models.market_products import MarketProduct
from app.models.recipes import Recipe
from app.models.florists import Florist
from app.models.orders import Order, OrderItem
from app.models.stock_movements import StockMovement
from app.models.print_jobs import PrintJob
from app.models.price_history import PriceHistory
from app.models.price_alerts import PriceAlert
from app.models.promo_participations import PromoParticipation
from app.models.economics_reports import EconomicsReport
from app.models.shop_schedule import ShopSchedule
from app.models.events_log import EventLog

__all__ = [
    "Base", "RawMaterial", "MarketProduct", "Recipe", "Florist",
    "Order", "OrderItem", "StockMovement", "PrintJob",
    "PriceHistory", "PriceAlert", "PromoParticipation",
    "EconomicsReport", "ShopSchedule", "EventLog",
]
```

- [ ] **Step 7: Run tests — expect pass**

```bash
pytest tests/test_models.py::test_market_product_has_is_pr tests/test_models.py::test_price_history_has_promo_price tests/test_models.py::test_promo_participation_create -v
```

Expected: 3 passed.

- [ ] **Step 8: Generate and apply Alembic migration**

```bash
# On VPS — after git pull:
docker compose exec app alembic revision --autogenerate -m "add_pricing_agent_tables"
docker compose exec app alembic upgrade head
```

Locally (for dev), set `POSTGRES_HOST=localhost` and run:
```bash
POSTGRES_HOST=localhost alembic revision --autogenerate -m "add_pricing_agent_tables"
POSTGRES_HOST=localhost alembic upgrade head
```

- [ ] **Step 9: Commit**

```bash
git add app/models/market_products.py app/models/price_history.py \
        app/models/promo_participations.py app/models/__init__.py \
        tests/agents/pricing_agent/__init__.py tests/test_models.py \
        alembic/versions/
git commit -m "feat(pricing): add is_pr, promo_price, promo_participations models"
```

---

## Task 2: Update sheets_loader for `is_pr`

**Files:**
- Modify: `app/agents/flower_stock/sheets_loader.py`
- Test: `tests/agents/flower_stock/test_agent.py` (extend existing)

- [ ] **Step 1: Write failing test**

Add to `tests/agents/flower_stock/test_stock_ops.py` (or create new file `tests/agents/flower_stock/test_sheets_loader.py`):

```python
import pytest
from decimal import Decimal
from unittest.mock import MagicMock, AsyncMock, patch
from app.agents.flower_stock.sheets_loader import load_products


@pytest.mark.asyncio
async def test_load_products_sets_is_pr_true():
    rows = [
        ["SKU-001", "Роза красная", "1500", "2100", "1000", "1000", "pr"],
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct:
        instance = MagicMock()
        MockProduct.return_value = instance
        result = await load_products(session, rows)

    assert instance.is_pr is True


@pytest.mark.asyncio
async def test_load_products_sets_is_pr_false_by_default():
    rows = [
        ["SKU-002", "Тюльпан", "750", "1050", "500", "500", ""],
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct:
        instance = MagicMock()
        MockProduct.return_value = instance
        result = await load_products(session, rows)

    assert instance.is_pr is False
```

- [ ] **Step 2: Run test — expect failure**

```bash
pytest tests/agents/flower_stock/test_sheets_loader.py -v
```

Expected: `AssertionError` (is_pr not set).

- [ ] **Step 3: Update `load_products` in `app/agents/flower_stock/sheets_loader.py`**

Change the `load_products` function to read column G and set `is_pr`. Replace the existing function body (lines 74–108):

```python
async def load_products(
    db: AsyncSession, rows: list[list]
) -> dict[str, MarketProduct]:
    """Upsert market_products from sheet rows. Returns {market_sku: MarketProduct}."""
    loaded: dict[str, MarketProduct] = {}
    for row in rows:
        if len(row) < 6 or not row[0].strip():
            continue
        sku = row[0].strip()
        name = row[1].strip()
        catalog_price, crossed_price = _d(row[2]), _d(row[3])
        min_price, optimal_price = _d(row[4]), _d(row[5])
        is_pr = len(row) > 6 and str(row[6]).strip().lower() in ("pr", "true", "1", "да")

        result = await db.execute(
            select(MarketProduct).where(MarketProduct.market_sku == sku)
        )
        prod = result.scalar_one_or_none()
        if prod is None:
            prod = MarketProduct(
                market_sku=sku, name=name,
                catalog_price=catalog_price, crossed_price=crossed_price,
                min_price=min_price, optimal_price=optimal_price,
                is_pr=is_pr,
            )
            db.add(prod)
        else:
            prod.name = name
            prod.catalog_price = catalog_price
            prod.crossed_price = crossed_price
            prod.min_price = min_price
            prod.optimal_price = optimal_price
            prod.is_pr = is_pr
        await db.commit()
        await db.refresh(prod)
        loaded[sku] = prod
        logger.info("Loaded product: %s — %s (is_pr=%s)", sku, name, is_pr)
    return loaded
```

Also update `load_from_sheets` to read `Товары!A2:G` (was A2:F):

```python
prod_rows = _get_range(service, spreadsheet_id, "Товары!A2:G")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/agents/flower_stock/test_sheets_loader.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/agents/flower_stock/sheets_loader.py tests/agents/flower_stock/test_sheets_loader.py
git commit -m "feat(sheets): read is_pr column G from Товары sheet"
```

---

## Task 3: Market API Layer

**Files:**
- Create: `app/agents/pricing_agent/__init__.py`
- Create: `app/agents/pricing_agent/market_api.py`
- Create: `tests/agents/pricing_agent/test_market_api.py`

- [ ] **Step 1: Create empty `__init__.py`**

```bash
touch app/agents/pricing_agent/__init__.py
touch tests/agents/pricing_agent/__init__.py
```

- [ ] **Step 2: Write tests for market_api**

Create `tests/agents/pricing_agent/test_market_api.py`:

```python
import pytest
import io
import csv
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
```

- [ ] **Step 3: Run tests — expect ImportError (module doesn't exist yet)**

```bash
pytest tests/agents/pricing_agent/test_market_api.py -v
```

Expected: `ImportError: cannot import name 'generate_prices_report' from 'app.agents.pricing_agent.market_api'`

- [ ] **Step 4: Create `app/agents/pricing_agent/market_api.py`**

```python
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
```

- [ ] **Step 5: Run tests — expect pass**

```bash
pytest tests/agents/pricing_agent/test_market_api.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add app/agents/pricing_agent/__init__.py app/agents/pricing_agent/market_api.py \
        tests/agents/pricing_agent/__init__.py tests/agents/pricing_agent/test_market_api.py
git commit -m "feat(pricing): add market_api layer (prices report, promos, catalog updates)"
```

---

## Task 4: Price Engine (Business Logic)

**Files:**
- Create: `app/agents/pricing_agent/price_engine.py`
- Create: `tests/agents/pricing_agent/test_price_engine.py`

- [ ] **Step 1: Write tests for price engine**

Create `tests/agents/pricing_agent/test_price_engine.py`:

```python
from decimal import Decimal
import pytest

from app.agents.pricing_agent.price_engine import (
    PROMO_FLOOR_MULT,
    PROMO_STEP_MULT,
    STOREFRONT_WATCH_MULT,
    compute_promo_floor,
    compute_new_promo_price,
    should_lower_promo_price,
    should_alert_below_optimal,
    compute_catalog_update,
    is_quarantine_risk,
    CatalogUpdate,
    StorefrontDecision,
    evaluate_storefront,
)


def test_compute_promo_floor():
    assert compute_promo_floor(Decimal("1000")) == Decimal("1100")


def test_compute_new_promo_price_normal():
    # current=1500, step=120, floor=1100 → 1380
    result = compute_new_promo_price(Decimal("1500"), Decimal("1000"))
    assert result == Decimal("1380")


def test_compute_new_promo_price_respects_floor():
    # current=1150, step=120, floor=1100 → clamped to 1100
    result = compute_new_promo_price(Decimal("1150"), Decimal("1000"))
    assert result == Decimal("1100")


def test_should_lower_promo_price_true_when_above_watch():
    # storefront=1200 > optimal*1.05=1050 → True
    assert should_lower_promo_price(Decimal("1200"), Decimal("1000")) is True


def test_should_lower_promo_price_false_when_at_watch():
    # storefront=1050 == optimal*1.05 → False
    assert should_lower_promo_price(Decimal("1050"), Decimal("1000")) is False


def test_should_alert_below_optimal():
    assert should_alert_below_optimal(Decimal("999"), Decimal("1000")) is True
    assert should_alert_below_optimal(Decimal("1000"), Decimal("1000")) is False


def test_compute_catalog_update_detects_mismatch():
    update = compute_catalog_update(
        sku="SKU-001",
        db_catalog=Decimal("1500"),
        db_crossed=Decimal("2100"),
        db_optimal=Decimal("1000"),
        market_catalog=Decimal("1400"),
    )
    assert update is not None
    assert update.sku == "SKU-001"
    assert update.new_value == Decimal("1500")


def test_compute_catalog_update_no_change_when_equal():
    result = compute_catalog_update(
        sku="SKU-001",
        db_catalog=Decimal("1500"),
        db_crossed=Decimal("2100"),
        db_optimal=Decimal("1000"),
        market_catalog=Decimal("1500"),
    )
    assert result is None


def test_is_quarantine_risk_large_drop():
    assert is_quarantine_risk(Decimal("2500"), Decimal("1800")) is True


def test_is_quarantine_risk_small_drop():
    assert is_quarantine_risk(Decimal("2500"), Decimal("2200")) is False


def test_evaluate_storefront_lower_action():
    decision = evaluate_storefront(
        sku="SKU-001",
        storefront=Decimal("1300"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1200"),
        is_pr=False,
    )
    assert decision.action == "lower"
    assert decision.new_promo_price == Decimal("1080")  # max(1200-120, 1100)


def test_evaluate_storefront_below_min_action():
    decision = evaluate_storefront(
        sku="SKU-001",
        storefront=Decimal("900"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1200"),
        is_pr=False,
    )
    assert decision.action == "alert_below_optimal"


def test_evaluate_storefront_pr_sku_no_lower():
    decision = evaluate_storefront(
        sku="PR-001",
        storefront=Decimal("1500"),
        optimal=Decimal("1000"),
        current_promo=Decimal("1800"),
        is_pr=True,
    )
    assert decision.action == "skip"
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
pytest tests/agents/pricing_agent/test_price_engine.py -v
```

Expected: `ImportError` (module not found).

- [ ] **Step 3: Create `app/agents/pricing_agent/price_engine.py`**

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

PROMO_FLOOR_MULT = Decimal("1.10")
PROMO_STEP_MULT = Decimal("0.12")
STOREFRONT_WATCH_MULT = Decimal("1.05")
QUARANTINE_THRESHOLD = Decimal("0.20")


def compute_promo_floor(optimal: Decimal) -> Decimal:
    return optimal * PROMO_FLOOR_MULT


def compute_promo_step(optimal: Decimal) -> Decimal:
    return optimal * PROMO_STEP_MULT


def compute_new_promo_price(current_promo: Decimal, optimal: Decimal) -> Decimal:
    floor = compute_promo_floor(optimal)
    step = compute_promo_step(optimal)
    return max(current_promo - step, floor)


def should_lower_promo_price(storefront: Decimal, optimal: Decimal) -> bool:
    return storefront > optimal * STOREFRONT_WATCH_MULT


def should_alert_below_optimal(storefront: Decimal, optimal: Decimal) -> bool:
    return storefront < optimal


def is_quarantine_risk(current_catalog: Decimal, new_catalog: Decimal) -> bool:
    if current_catalog <= 0:
        return False
    drop_ratio = (current_catalog - new_catalog) / current_catalog
    return drop_ratio > QUARANTINE_THRESHOLD


@dataclass
class CatalogUpdate:
    sku: str
    new_value: Decimal
    new_discount_base: Decimal
    minimum_for_bestseller: Decimal
    quarantine_risk: bool


def compute_catalog_update(
    sku: str,
    db_catalog: Decimal,
    db_crossed: Decimal,
    db_optimal: Decimal,
    market_catalog: Decimal,
) -> Optional[CatalogUpdate]:
    if db_catalog == market_catalog:
        return None
    return CatalogUpdate(
        sku=sku,
        new_value=db_catalog,
        new_discount_base=db_crossed,
        minimum_for_bestseller=db_optimal,
        quarantine_risk=is_quarantine_risk(market_catalog, db_catalog),
    )


@dataclass
class StorefrontDecision:
    sku: str
    action: str  # "lower" | "alert_below_optimal" | "alert_floor_breach" | "skip" | "ok"
    new_promo_price: Optional[Decimal] = None
    storefront: Optional[Decimal] = None
    optimal: Optional[Decimal] = None


def evaluate_storefront(
    sku: str,
    storefront: Decimal,
    optimal: Decimal,
    current_promo: Decimal,
    is_pr: bool,
) -> StorefrontDecision:
    if is_pr:
        return StorefrontDecision(sku=sku, action="skip")

    floor = compute_promo_floor(optimal)

    if current_promo < floor:
        return StorefrontDecision(
            sku=sku, action="alert_floor_breach",
            storefront=storefront, optimal=optimal,
        )

    if should_alert_below_optimal(storefront, optimal):
        return StorefrontDecision(
            sku=sku, action="alert_below_optimal",
            storefront=storefront, optimal=optimal,
        )

    if should_lower_promo_price(storefront, optimal):
        new_promo = compute_new_promo_price(current_promo, optimal)
        return StorefrontDecision(
            sku=sku, action="lower",
            new_promo_price=new_promo,
            storefront=storefront, optimal=optimal,
        )

    return StorefrontDecision(sku=sku, action="ok")
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/agents/pricing_agent/test_price_engine.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add app/agents/pricing_agent/price_engine.py \
        tests/agents/pricing_agent/test_price_engine.py
git commit -m "feat(pricing): add price_engine business logic with full test coverage"
```

---

## Task 5: PricingAgent

**Files:**
- Create: `app/agents/pricing_agent/agent.py`
- Create: `tests/agents/pricing_agent/test_agent.py`

- [ ] **Step 1: Write tests for agent**

Create `tests/agents/pricing_agent/test_agent.py`:

```python
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.pricing_agent.agent import PricingAgent


def _make_agent(db_factory=None, owner_bot=None, settings=None, scheduler=None):
    if db_factory is None:
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        session_mock.execute = AsyncMock(return_value=MagicMock(
            scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ))
        db_factory = MagicMock(return_value=session_mock)
    if owner_bot is None:
        owner_bot = AsyncMock()
        owner_bot.send_message = AsyncMock()
    if settings is None:
        settings = MagicMock()
        settings.owner_telegram_id = 111111
        settings.market_api_token = "test_token"
        settings.market_business_id = 187548892
    if scheduler is None:
        scheduler = MagicMock()
        scheduler.add_job = MagicMock()
    return PricingAgent(db_factory, owner_bot, settings, scheduler)


@pytest.mark.asyncio
async def test_schedule_registers_job():
    scheduler = MagicMock()
    scheduler.add_job = MagicMock()
    agent = _make_agent(scheduler=scheduler)
    agent.schedule()
    scheduler.add_job.assert_called_once()
    call_kwargs = scheduler.add_job.call_args.kwargs
    assert call_kwargs.get("hours") == 3
    assert call_kwargs.get("max_instances") == 1


@pytest.mark.asyncio
async def test_run_cycle_continues_when_report_fails():
    agent = _make_agent()

    with patch("app.agents.pricing_agent.agent.market_api") as mock_api:
        mock_api.fetch_storefront_prices = AsyncMock(
            side_effect=Exception("report timeout")
        )
        mock_api.get_promos = AsyncMock(return_value=[])
        mock_api.get_promo_offers = AsyncMock(return_value=[])
        mock_api.update_catalog_prices = AsyncMock()

        await agent.run_cycle()

    agent._owner_bot.send_message.assert_called()
    call_text = agent._owner_bot.send_message.call_args.args[1]
    assert "Отчёт витрины" in call_text or "витрин" in call_text.lower()


@pytest.mark.asyncio
async def test_run_cycle_sends_summary_when_no_changes():
    agent = _make_agent()

    with patch("app.agents.pricing_agent.agent.market_api") as mock_api:
        mock_api.fetch_storefront_prices = AsyncMock(return_value={})
        mock_api.get_promos = AsyncMock(return_value=[])
        mock_api.get_promo_offers = AsyncMock(return_value=[])
        mock_api.update_catalog_prices = AsyncMock()

        await agent.run_cycle()

    agent._owner_bot.send_message.assert_not_called()
```

- [ ] **Step 2: Run tests — expect ImportError**

```bash
pytest tests/agents/pricing_agent/test_agent.py -v
```

Expected: `ImportError` (agent.py not found).

- [ ] **Step 3: Create `app/agents/pricing_agent/agent.py`**

```python
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.pricing_agent import market_api
from app.agents.pricing_agent.price_engine import (
    compute_catalog_update,
    evaluate_storefront,
    compute_promo_floor,
    CatalogUpdate,
    StorefrontDecision,
)
from app.models.market_products import MarketProduct
from app.models.price_history import PriceHistory
from app.models.price_alerts import PriceAlert
from app.models.promo_participations import PromoParticipation

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    catalog_synced: int = 0
    promo_adjusted: int = 0
    alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    quarantine_pending: list[CatalogUpdate] = field(default_factory=list)


class PricingAgent:
    def __init__(
        self,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
        scheduler,
    ):
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings
        self._scheduler = scheduler

    def schedule(self) -> None:
        self._scheduler.add_job(
            func=self.run_cycle,
            trigger="interval",
            hours=3,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            id="pricing_agent_cycle",
        )
        logger.info("PricingAgent scheduled every 3 hours")

    async def _alert(self, text: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, text)
        except Exception as exc:
            logger.error("Alert send failed: %s", exc)

    async def _load_products(self) -> list[MarketProduct]:
        async with self._db_factory() as db:
            result = await db.execute(select(MarketProduct))
            return result.scalars().all()

    async def _load_promo_cache(self) -> dict[str, dict[str, Decimal]]:
        """Returns {promo_id: {product_id_str: promo_price}}."""
        async with self._db_factory() as db:
            result = await db.execute(select(PromoParticipation))
            cache: dict[str, dict[str, Decimal]] = {}
            for pp in result.scalars().all():
                cache.setdefault(pp.promo_id, {})[str(pp.product_id)] = pp.promo_price
        return cache

    async def _save_price_history(
        self,
        products: list[MarketProduct],
        storefront_prices: dict[str, Decimal],
        promo_prices: dict[str, Decimal],
    ) -> None:
        async with self._db_factory() as db:
            for prod in products:
                db.add(PriceHistory(
                    product_id=prod.id,
                    catalog_price=prod.catalog_price,
                    storefront_price=storefront_prices.get(prod.market_sku),
                    min_price=prod.min_price,
                    optimal_price=prod.optimal_price,
                    promo_price=promo_prices.get(prod.market_sku),
                ))
            await db.commit()

    async def _save_alert(self, product_id, alert_type: str, message: str) -> None:
        async with self._db_factory() as db:
            db.add(PriceAlert(
                product_id=product_id,
                type=alert_type,
                message=message,
            ))
            await db.commit()

    # ─── Phase 2: Catalog sync ───────────────────────────────────────────────

    async def _phase_catalog_sync(
        self,
        products: list[MarketProduct],
        result: CycleResult,
    ) -> None:
        safe_updates: list[dict] = []

        for prod in products:
            update = compute_catalog_update(
                sku=prod.market_sku,
                db_catalog=prod.catalog_price,
                db_crossed=prod.crossed_price,
                db_optimal=prod.optimal_price,
                market_catalog=prod.catalog_price,
            )
            if update is None:
                continue
            if update.quarantine_risk:
                result.quarantine_pending.append(update)
                continue
            safe_updates.append({
                "sku": update.sku,
                "value": update.new_value,
                "discount_base": update.new_discount_base,
                "minimum_for_bestseller": update.minimum_for_bestseller,
            })

        if safe_updates:
            try:
                await market_api.update_catalog_prices(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    safe_updates,
                )
                result.catalog_synced = len(safe_updates)
            except Exception as exc:
                logger.error("Catalog sync failed: %s", exc)
                result.errors.append(f"Sync каталога: {exc}")

    # ─── Phase 3: Storefront monitoring ─────────────────────────────────────

    async def _phase_storefront(
        self,
        products: list[MarketProduct],
        storefront_prices: dict[str, Decimal],
        promo_cache: dict[str, dict[str, Decimal]],
        result: CycleResult,
    ) -> None:
        current_promos: dict[str, Decimal] = {}
        for promo_data in promo_cache.values():
            for pid, price in promo_data.items():
                if price is not None:
                    current_promos[pid] = price

        promo_updates: list[tuple[str, str, Decimal]] = []  # (promo_id, sku, new_price)

        for prod in products:
            storefront = storefront_prices.get(prod.market_sku)
            if storefront is None:
                continue
            current_promo = current_promos.get(str(prod.id))
            if current_promo is None:
                continue

            decision = evaluate_storefront(
                sku=prod.market_sku,
                storefront=storefront,
                optimal=prod.optimal_price,
                current_promo=current_promo,
                is_pr=prod.is_pr,
            )

            if decision.action == "lower":
                for promo_id, pdata in promo_cache.items():
                    if str(prod.id) in pdata:
                        promo_updates.append((promo_id, prod.market_sku, decision.new_promo_price))
                result.promo_adjusted += 1

            elif decision.action == "alert_below_optimal":
                msg = (
                    f"ℹ️ Витрина ниже optimal (Яндекс платит разницу)\n\n"
                    f"{prod.name}: витрина {storefront}₽ / optimal {prod.optimal_price}₽\n"
                    f"  Наш promoPrice: {current_promo}₽ ✓\n"
                    f"  Разницу {prod.optimal_price - storefront}₽ покрывает Яндекс."
                )
                result.alerts.append(f"{prod.name}: витрина {storefront}₽ < optimal")
                await self._save_alert(prod.id, "below_min", msg)

            elif decision.action == "alert_floor_breach":
                msg = (
                    f"🚨 promoPrice ниже минимума\n\n"
                    f"{prod.name}: promoPrice {current_promo}₽ < порог "
                    f"{compute_promo_floor(prod.optimal_price)}₽ (optimal × 1.10)"
                )
                result.alerts.append(f"{prod.name}: promoPrice ниже порога")
                await self._alert(msg)

        for promo_id, sku, new_price in promo_updates:
            try:
                await market_api.update_promo_offers(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    promo_id,
                    [{"sku": sku, "promo_price": new_price}],
                )
            except Exception as exc:
                logger.error("promoPrice update failed %s: %s", sku, exc)
                result.errors.append(f"promoPrice {sku}: {exc}")

    # ─── Phase 4: Promo management ───────────────────────────────────────────

    async def _phase_promo_management(
        self,
        products: list[MarketProduct],
        available_promos: list[dict],
        promo_cache: dict[str, dict[str, Decimal]],
        result: CycleResult,
    ) -> None:
        product_by_id = {str(p.id): p for p in products}

        for promo in available_promos:
            promo_id = promo.get("id") or promo.get("promoId", "")
            promo_type = promo.get("mechanicsType", "")

            is_fixed = "DIRECT_DISCOUNT" not in promo_type and "CHEAPEST_AS_GIFT" not in promo_type
            is_direct = "DIRECT_DISCOUNT" in promo_type

            cached = promo_cache.get(promo_id, {})
            offers_to_add: list[dict] = []

            for prod in products:
                already_in = str(prod.id) in cached
                if already_in:
                    continue

                if is_fixed:
                    offers_to_add.append({"sku": prod.market_sku, "promo_price": None})
                elif is_direct:
                    floor = compute_promo_floor(prod.optimal_price)
                    if prod.is_pr:
                        offers_to_add.append({"sku": prod.market_sku, "promo_price": prod.catalog_price})
                    else:
                        offers_to_add.append({"sku": prod.market_sku, "promo_price": floor})

            if not offers_to_add:
                continue

            try:
                api_result = await market_api.update_promo_offers(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    promo_id,
                    offers_to_add,
                )
                rejected_skus = {r.get("offerId") for r in api_result.get("rejected", [])}
                for rej in api_result.get("rejected", []):
                    sku = rej.get("offerId", "")
                    reason = rej.get("reason", "")
                    result.alerts.append(f"{sku}: Яндекс отклонил участие в акции ({reason})")

                # Persist accepted offers to promo_participations cache
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                async with self._db_factory() as db:
                    for offer in offers_to_add:
                        if offer["sku"] in rejected_skus:
                            continue
                        prod = next((p for p in products if p.market_sku == offer["sku"]), None)
                        if prod is None:
                            continue
                        db.add(PromoParticipation(
                            product_id=prod.id,
                            promo_id=promo_id,
                            promo_type="fixed_discount" if is_fixed else "direct_discount",
                            promo_price=offer.get("promo_price"),
                            discount_pct=None,
                            updated_at=now,
                        ))
                    await db.commit()
            except Exception as exc:
                logger.error("Promo management failed for %s: %s", promo_id, exc)
                result.errors.append(f"Акция {promo_id}: {exc}")

    # ─── Phase 6: Telegram summary ───────────────────────────────────────────

    async def _send_summary(self, result: CycleResult) -> None:
        has_content = (
            result.catalog_synced > 0
            or result.promo_adjusted > 0
            or result.alerts
            or result.errors
            or result.quarantine_pending
        )
        if not has_content:
            return

        lines = ["📊 Pricing Agent — цикл завершён\n"]
        if result.catalog_synced:
            lines.append(f"✅ Синхронизировано цен: {result.catalog_synced} SKU")
        if result.promo_adjusted:
            lines.append(f"✅ promoPrice скорректирован: {result.promo_adjusted} SKU")
        if result.errors:
            lines.append("")
            for e in result.errors:
                lines.append(f"❌ {e}")
        if result.alerts:
            lines.append(f"\n⚠️ Требуют внимания: {len(result.alerts)} SKU")
            for a in result.alerts[:10]:
                lines.append(f"  — {a}")
            if len(result.alerts) > 10:
                lines.append(f"  … и ещё {len(result.alerts) - 10}")

        await self._alert("\n".join(lines))

        for update in result.quarantine_pending:
            await self._send_quarantine_alert(update)

    async def _send_quarantine_alert(self, update: CatalogUpdate) -> None:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        text = (
            f"⚠️ Риск карантина\n\n"
            f"{update.sku}: текущая {update.new_value}₽ — резкое изменение цены\n"
            f"Яндекс может скрыть товар до ручного подтверждения.\n\n"
            f"Применить изменение?"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Да, обновить",
                callback_data=f"price_quarantine_confirm:{update.sku}"
            ),
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"price_quarantine_skip:{update.sku}"
            ),
        ]])
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, text, reply_markup=keyboard
            )
        except Exception as exc:
            logger.error("Quarantine alert failed: %s", exc)

    # ─── Main cycle ──────────────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        logger.info("PricingAgent cycle started")
        result = CycleResult()

        products = await self._load_products()
        if not products:
            logger.info("No products in DB — skipping cycle")
            return

        promo_cache = await self._load_promo_cache()

        # Phase 1: Fetch storefront prices (async report)
        storefront_prices: dict[str, Decimal] = {}
        try:
            storefront_prices = await market_api.fetch_storefront_prices(
                self._settings.market_business_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("Storefront report failed: %s", exc)
            result.errors.append(f"Отчёт витрины недоступен: {exc}")
            await self._alert(f"⚠️ Отчёт витрины недоступен — мониторинг пропущен\n{exc}")

        # Phase 1b: Fetch available promos
        available_promos: list[dict] = []
        try:
            available_promos = await market_api.get_promos(
                self._settings.market_business_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("get_promos failed: %s", exc)
            result.errors.append(f"Список акций недоступен: {exc}")

        # Phase 2: Catalog sync
        try:
            await self._phase_catalog_sync(products, result)
        except Exception as exc:
            logger.error("_phase_catalog_sync crashed: %s", exc)
            result.errors.append(f"Фаза 2 упала: {exc}")

        # Phase 3: Storefront monitoring (only if report succeeded)
        if storefront_prices:
            promo_prices: dict[str, Decimal] = {}
            for promo_data in promo_cache.values():
                for pid, price in promo_data.items():
                    if price:
                        promo_prices[pid] = price

            try:
                await self._phase_storefront(products, storefront_prices, promo_cache, result)
            except Exception as exc:
                logger.error("_phase_storefront crashed: %s", exc)
                result.errors.append(f"Фаза 3 упала: {exc}")

        # Phase 4: Promo management
        try:
            await self._phase_promo_management(products, available_promos, promo_cache, result)
        except Exception as exc:
            logger.error("_phase_promo_management crashed: %s", exc)
            result.errors.append(f"Фаза 4 упала: {exc}")

        # Phase 5: Save history
        try:
            sf_prices = storefront_prices if storefront_prices else {}
            # Build {market_sku: promo_price} from promo_cache (keyed by str(product_id))
            product_by_id = {str(p.id): p for p in products}
            current_promos: dict[str, Decimal] = {}
            for pdata in promo_cache.values():
                for pid_str, price in pdata.items():
                    if price and pid_str in product_by_id:
                        current_promos[product_by_id[pid_str].market_sku] = price
            await self._save_price_history(products, sf_prices, current_promos)
        except Exception as exc:
            logger.error("Price history save failed: %s", exc)

        # Phase 6: Summary
        await self._send_summary(result)
        logger.info(
            "PricingAgent cycle done: synced=%d adjusted=%d alerts=%d errors=%d",
            result.catalog_synced, result.promo_adjusted,
            len(result.alerts), len(result.errors),
        )
```

- [ ] **Step 4: Run tests — expect pass**

```bash
pytest tests/agents/pricing_agent/test_agent.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/agents/pricing_agent/agent.py tests/agents/pricing_agent/test_agent.py
git commit -m "feat(pricing): add PricingAgent with 6-phase cycle and APScheduler"
```

---

## Task 6: Telegram Quarantine Callback

**Files:**
- Modify: `app/bot/owner_bot.py`

- [ ] **Step 1: Add quarantine callback registration to `app/bot/owner_bot.py`**

Add a new `register_pricing_callbacks` function (after `register_stock_commands`):

```python
def register_pricing_callbacks(pricing_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith(("price_quarantine_confirm:", "price_quarantine_skip:"))
    )
    async def handle_quarantine_callback(callback: CallbackQuery):
        await callback.answer()
        action, sku = callback.data.split(":", 1)
        if action == "price_quarantine_confirm":
            await pricing_agent.apply_quarantine_update(sku)
            await callback.message.edit_text(f"✅ Цена для {sku} обновлена в Маркете.")
        else:
            await callback.message.edit_text(f"⏭ Обновление {sku} пропущено.")
```

- [ ] **Step 2: Add `apply_quarantine_update` to `app/agents/pricing_agent/agent.py`**

Add this method to the `PricingAgent` class (after `_send_quarantine_alert`):

```python
async def apply_quarantine_update(self, sku: str) -> None:
    async with self._db_factory() as db:
        result = await db.execute(
            select(MarketProduct).where(MarketProduct.market_sku == sku)
        )
        prod = result.scalar_one_or_none()
    if prod is None:
        logger.error("apply_quarantine_update: SKU %s not found", sku)
        return
    try:
        await market_api.update_catalog_prices(
            self._settings.market_business_id,
            self._settings.market_api_token,
            [{
                "sku": sku,
                "value": prod.catalog_price,
                "discount_base": prod.crossed_price,
                "minimum_for_bestseller": prod.optimal_price,
            }],
        )
        logger.info("Quarantine update applied for %s", sku)
    except Exception as exc:
        logger.error("Quarantine update failed for %s: %s", sku, exc)
        await self._alert(f"❌ Ошибка обновления цены {sku}: {exc}")
```

- [ ] **Step 3: Commit**

```bash
git add app/bot/owner_bot.py app/agents/pricing_agent/agent.py
git commit -m "feat(pricing): add quarantine confirm/skip Telegram callback"
```

---

## Task 7: Wire into `main.py`

**Files:**
- Modify: `app/main.py`
- Modify: `app/config.py` (add `market_business_id`)

- [ ] **Step 1: Add `market_business_id` to `app/config.py`**

```python
market_business_id: int = 187548892
```

Add this line after `market_warehouse_id` in the `Settings` class.

- [ ] **Step 2: Wire PricingAgent in `app/main.py`**

Add the import at the top:

```python
from app.agents.pricing_agent.agent import PricingAgent
from apscheduler.schedulers.asyncio import AsyncIOScheduler
```

Inside the `lifespan` function, after `flower_stock_agent` wiring, add:

```python
scheduler = AsyncIOScheduler()
pricing_agent = PricingAgent(AsyncSessionLocal, owner_bot, settings, scheduler)
pricing_agent.schedule()
scheduler.start()
```

Register quarantine callback in owner_bot:

```python
from app.bot.owner_bot import register_pricing_callbacks
# ...
register_pricing_callbacks(pricing_agent)
```

In the teardown section (after `yield`), add:

```python
scheduler.shutdown(wait=False)
```

- [ ] **Step 3: Verify app starts without error**

```bash
# locally — just import check
python -c "from app.main import app; print('OK')"
```

Expected: `OK` (no ImportError).

- [ ] **Step 4: Commit**

```bash
git add app/main.py app/config.py
git commit -m "feat(pricing): wire PricingAgent into app lifespan"
```

---

## Task 8: Full Test Suite Pass

- [ ] **Step 1: Run all tests**

```bash
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: all existing tests pass + new pricing tests pass. If any fail, fix before proceeding.

- [ ] **Step 2: Deploy to VPS**

```bash
# On VPS
cd ~/buds-agent
git pull
docker compose exec app alembic upgrade head
docker compose restart app
docker compose logs app --tail=50
```

Expected logs: `PricingAgent scheduled every 3 hours`, no ERROR lines.

- [ ] **Step 3: Trigger manual cycle to verify**

```bash
# On VPS — run one cycle immediately
docker compose exec app python -c "
import asyncio
from app.main import *
# trigger manually via HTTP or direct call
"
```

Or add a temporary `/admin/pricing-cycle` endpoint for testing, then remove it.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -p
git commit -m "fix(pricing): post-integration fixes"
```

---

## Checklist Summary

- [ ] Task 1: DB models (is_pr, promo_price, promo_participations)
- [ ] Task 2: sheets_loader reads is_pr from column G
- [ ] Task 3: market_api.py (report, promos, price updates)
- [ ] Task 4: price_engine.py (pure business logic)
- [ ] Task 5: agent.py (6-phase cycle)
- [ ] Task 6: quarantine Telegram callback
- [ ] Task 7: wire into main.py
- [ ] Task 8: all tests pass, deployed to VPS
