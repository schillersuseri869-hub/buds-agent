# BUDS Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set up the complete infrastructure skeleton for BUDS — Docker Compose (PostgreSQL + Redis + FastAPI app), all 13 database models with Alembic migrations, Redis event bus, aiogram bot skeletons, WebSocket print endpoint, and Phase 0 Yandex Market API discovery scripts.

**Architecture:** Single Docker Compose deployment (modular monolith). FastAPI handles HTTP webhooks and WebSocket. aiogram 3.x runs inline with FastAPI via `asyncio.create_task` polling. All 4 future agents share the same Python process and will communicate through `app/core/event_bus.py` (Redis pub/sub). PostgreSQL stores all state; Redis handles pub/sub and caching.

**Tech Stack:** Python 3.11+, FastAPI 0.115, aiogram 3.13, SQLAlchemy 2.0, Alembic 1.13, asyncpg 0.29, PostgreSQL 15, Redis 7, pydantic-settings 2.5, pytest 8.3, pytest-asyncio 0.24, fakeredis 2.26, httpx 0.27

---

## File Map

**Created (new files):**
- `requirements.txt`
- `Dockerfile`
- `docker-compose.yml`
- `.env.example`
- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/` — populated by `alembic revision --autogenerate`
- `app/__init__.py`
- `app/config.py`
- `app/database.py`
- `app/main.py`
- `app/models/__init__.py`
- `app/models/base.py`
- `app/models/raw_materials.py`
- `app/models/market_products.py`
- `app/models/recipes.py`
- `app/models/florists.py`
- `app/models/orders.py`
- `app/models/stock_movements.py`
- `app/models/print_jobs.py`
- `app/models/price_history.py`
- `app/models/price_alerts.py`
- `app/models/economics_reports.py`
- `app/models/shop_schedule.py`
- `app/models/events_log.py`
- `app/core/__init__.py`
- `app/core/event_bus.py`
- `app/api/__init__.py`
- `app/api/webhooks.py`
- `app/api/ws_print.py`
- `app/bot/__init__.py`
- `app/bot/owner_bot.py`
- `app/bot/florist_bot.py`
- `print_client/requirements.txt`
- `print_client/print_client.py`
- `scripts/test_market_api.py`
- `results/phase0_findings.md`
- `tests/__init__.py`
- `tests/conftest.py`
- `tests/test_config.py`
- `tests/test_models.py`
- `tests/test_event_bus.py`
- `tests/test_webhooks.py`

---

### Task 1: Project scaffold

**Files:**
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
aiogram==3.13.0
sqlalchemy==2.0.35
alembic==1.13.2
asyncpg==0.29.0
redis==5.0.8
httpx==0.27.2
beautifulsoup4==4.12.3
google-api-python-client==2.149.0
google-auth==2.35.0
google-auth-httplib2==0.2.0
apscheduler==3.10.4
python-escpos==3.1
websockets==13.0
python-dotenv==1.0.1
pydantic-settings==2.5.2
pytest==8.3.3
pytest-asyncio==0.24.0
fakeredis==2.26.1
```

- [ ] **Step 2: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 3: Create docker-compose.yml**

```yaml
version: '3.9'

services:
  app:
    build: .
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "8000:8000"
    volumes:
      - .:/app

  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: ${POSTGRES_DB:-buds}
      POSTGRES_USER: ${POSTGRES_USER:-buds}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-buds}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-buds}"]
      interval: 5s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 5

volumes:
  pgdata:
```

- [ ] **Step 4: Create .env.example**

```
# PostgreSQL
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=buds
POSTGRES_USER=buds
POSTGRES_PASSWORD=changeme

# Redis
REDIS_URL=redis://redis:6379/0

# Telegram
OWNER_BOT_TOKEN=
OWNER_TELEGRAM_ID=
FLORIST_BOT_TOKEN=
FLORIST_TELEGRAM_ID=

# Yandex Market API
MARKET_API_TOKEN=
MARKET_CAMPAIGN_ID=
MARKET_CLIENT_ID=

# Google Sheets Service Account
GOOGLE_SERVICE_ACCOUNT_FILE=/app/secrets/service_account.json
GOOGLE_SPREADSHEET_ID=
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt Dockerfile docker-compose.yml .env.example
git commit -m "feat: project scaffold — Docker Compose, Dockerfile, requirements"
```

---

### Task 2: App config and database session

**Files:**
- Create: `app/__init__.py`
- Create: `app/config.py`
- Create: `app/database.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

`tests/test_config.py`:
```python
import os
import pytest
from unittest.mock import patch


def test_settings_loads_from_env():
    env = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "buds_test",
        "POSTGRES_USER": "buds",
        "POSTGRES_PASSWORD": "secret",
        "REDIS_URL": "redis://localhost:6379/0",
        "OWNER_BOT_TOKEN": "123:abc",
        "OWNER_TELEGRAM_ID": "111222333",
        "FLORIST_BOT_TOKEN": "456:def",
        "FLORIST_TELEGRAM_ID": "444555666",
        "MARKET_API_TOKEN": "mtoken",
        "MARKET_CAMPAIGN_ID": "12345",
        "MARKET_CLIENT_ID": "67890",
        "GOOGLE_SERVICE_ACCOUNT_FILE": "/tmp/sa.json",
        "GOOGLE_SPREADSHEET_ID": "sheet123",
    }
    with patch.dict(os.environ, env, clear=True):
        from app.config import Settings
        s = Settings()
        assert s.postgres_db == "buds_test"
        assert s.redis_url == "redis://localhost:6379/0"
        assert s.owner_telegram_id == 111222333
        assert s.market_campaign_id == 12345


def test_database_url_format():
    env = {
        "POSTGRES_HOST": "myhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_DB": "mydb",
        "POSTGRES_USER": "myuser",
        "POSTGRES_PASSWORD": "mypass",
        "REDIS_URL": "redis://localhost:6379/0",
        "OWNER_BOT_TOKEN": "123:abc",
        "OWNER_TELEGRAM_ID": "1",
        "FLORIST_BOT_TOKEN": "456:def",
        "FLORIST_TELEGRAM_ID": "2",
        "MARKET_API_TOKEN": "t",
        "MARKET_CAMPAIGN_ID": "1",
        "MARKET_CLIENT_ID": "c",
    }
    with patch.dict(os.environ, env, clear=True):
        from app.config import Settings
        s = Settings()
        assert s.database_url == "postgresql+asyncpg://myuser:mypass@myhost:5432/mydb"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.config'`

- [ ] **Step 3: Create app/__init__.py** (empty file)

- [ ] **Step 4: Create app/config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "buds"
    postgres_user: str = "buds"
    postgres_password: str = "buds"
    redis_url: str = "redis://redis:6379/0"
    owner_bot_token: str
    owner_telegram_id: int
    florist_bot_token: str
    florist_telegram_id: int
    market_api_token: str
    market_campaign_id: int
    market_client_id: str
    google_service_account_file: str = "/app/secrets/service_account.json"
    google_spreadsheet_id: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
```

- [ ] **Step 5: Create app/database.py**

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

- [ ] **Step 6: Create tests/__init__.py** (empty file)

- [ ] **Step 7: Create tests/conftest.py**

```python
import os
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

TEST_DB_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://buds:buds@localhost:5432/buds_test",
)


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    from app.models.base import Base
    from app.models import *  # noqa: F401,F403 — registers all models with Base.metadata
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine):
    AsyncSession = async_sessionmaker(test_engine, expire_on_commit=False)
    async with AsyncSession() as session:
        yield session
        await session.rollback()
```

- [ ] **Step 8: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```
Expected: 2 tests PASSED

- [ ] **Step 9: Commit**

```bash
git add app/__init__.py app/config.py app/database.py tests/__init__.py tests/conftest.py tests/test_config.py
git commit -m "feat: config (pydantic-settings) and async database session"
```

---

### Task 3: SQLAlchemy models

**Files:**
- Create: `app/models/base.py`
- Create: `app/models/raw_materials.py`
- Create: `app/models/market_products.py`
- Create: `app/models/recipes.py`
- Create: `app/models/florists.py`
- Create: `app/models/orders.py`
- Create: `app/models/stock_movements.py`
- Create: `app/models/print_jobs.py`
- Create: `app/models/price_history.py`
- Create: `app/models/price_alerts.py`
- Create: `app/models/economics_reports.py`
- Create: `app/models/shop_schedule.py`
- Create: `app/models/events_log.py`
- Create: `app/models/__init__.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
import uuid
from decimal import Decimal
import pytest
from sqlalchemy import select

from app.models.raw_materials import RawMaterial
from app.models.market_products import MarketProduct
from app.models.recipes import Recipe
from app.models.florists import Florist
from app.models.orders import Order, OrderItem
from app.models.stock_movements import StockMovement
from app.models.print_jobs import PrintJob
from app.models.price_history import PriceHistory
from app.models.price_alerts import PriceAlert
from app.models.economics_reports import EconomicsReport
from app.models.shop_schedule import ShopSchedule
from app.models.events_log import EventLog


@pytest.mark.asyncio
async def test_raw_material_available_calc(db_session):
    mat = RawMaterial(
        name="Роза 40см",
        type="flower",
        unit="шт",
        physical_stock=Decimal("50"),
        reserved=Decimal("30"),
        cost_per_unit=Decimal("80.00"),
    )
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    assert mat.available == Decimal("18")  # 50 - 30 - 2


@pytest.mark.asyncio
async def test_order_with_items(db_session):
    product = MarketProduct(
        market_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Букет из 5 роз",
        catalog_price=Decimal("1500.00"),
        crossed_price=Decimal("2100.00"),
        min_price=Decimal("1650.00"),
        optimal_price=Decimal("1800.00"),
    )
    db_session.add(product)
    await db_session.flush()

    order = Order(
        market_order_id=f"YM-{uuid.uuid4().hex[:8]}",
        sale_price=Decimal("1500.00"),
        estimated_commission_pct=Decimal("15.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        unit_price=Decimal("1500.00"),
    )
    db_session.add(item)
    await db_session.commit()

    result = await db_session.execute(select(Order).where(Order.id == order.id))
    saved = result.scalar_one()
    assert saved.status == "waiting"
    assert saved.sale_price == Decimal("1500.00")


@pytest.mark.asyncio
async def test_stock_movement_types(db_session):
    mat = RawMaterial(
        name="Хризантема",
        type="flower",
        unit="шт",
        physical_stock=Decimal("20"),
        reserved=Decimal("0"),
        cost_per_unit=Decimal("50.00"),
    )
    db_session.add(mat)
    await db_session.flush()

    movement = StockMovement(
        material_id=mat.id,
        type="arrival",
        quantity=Decimal("20"),
        cost=Decimal("1000.00"),
    )
    db_session.add(movement)
    await db_session.commit()
    assert movement.id is not None


@pytest.mark.asyncio
async def test_events_log(db_session):
    log = EventLog(
        event_type="order.created",
        payload={"order_id": "YM-999"},
    )
    db_session.add(log)
    await db_session.commit()
    assert log.id is not None


@pytest.mark.asyncio
async def test_recipe_links_product_and_material(db_session):
    product = MarketProduct(
        market_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Тест-букет",
        catalog_price=Decimal("500"),
        crossed_price=Decimal("700"),
        min_price=Decimal("550"),
        optimal_price=Decimal("600"),
    )
    material = RawMaterial(
        name="Тюльпан",
        type="flower",
        unit="шт",
        physical_stock=Decimal("100"),
        reserved=Decimal("0"),
        cost_per_unit=Decimal("40"),
    )
    db_session.add_all([product, material])
    await db_session.flush()

    recipe = Recipe(product_id=product.id, material_id=material.id, quantity=Decimal("5"))
    db_session.add(recipe)
    await db_session.commit()
    assert recipe.id is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_models.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Create app/models/base.py**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 4: Create app/models/raw_materials.py**

```python
import uuid
from decimal import Decimal
from datetime import date
from sqlalchemy import String, Numeric, Date, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class RawMaterial(Base):
    __tablename__ = "raw_materials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum("flower", "consumable", name="raw_material_type"), nullable=False
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    physical_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    cost_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    last_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    @property
    def available(self) -> Decimal:
        return self.physical_stock - self.reserved - Decimal("2")
```

- [ ] **Step 5: Create app/models/market_products.py**

```python
import uuid
from decimal import Decimal
from sqlalchemy import String, Numeric, Enum
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
    status: Mapped[str] = mapped_column(
        Enum("active", "hidden", name="product_status"), nullable=False, default="active"
    )
```

- [ ] **Step 6: Create app/models/recipes.py**

```python
import uuid
from decimal import Decimal
from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_materials.id"), nullable=False, index=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
```

- [ ] **Step 7: Create app/models/florists.py**

```python
import uuid
from sqlalchemy import String, BigInteger, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class Florist(Base):
    __tablename__ = "florists"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

- [ ] **Step 8: Create app/models/orders.py**

```python
import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import String, Numeric, Enum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    market_order_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "waiting", "ready", "shipped", "delivered", "cancelled", "timed_out",
            name="order_status",
        ),
        nullable=False,
        default="waiting",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    timer_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_commission_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    actual_services_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_payout: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False
    )
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
```

- [ ] **Step 9: Create app/models/stock_movements.py**

```python
import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import Numeric, Enum, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class StockMovement(Base):
    __tablename__ = "stock_movements"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("raw_materials.id"), nullable=False, index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    type: Mapped[str] = mapped_column(
        Enum(
            "arrival", "reserve", "debit", "spoilage", "return", "release", "extra_debit",
            name="movement_type",
        ),
        nullable=False,
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    cost: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 10: Create app/models/print_jobs.py**

```python
import uuid
from datetime import datetime
from sqlalchemy import String, Enum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "sent", "done", "failed", name="print_job_status"),
        nullable=False,
        default="pending",
    )
    label_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 11: Create app/models/price_history.py**

```python
import uuid
from decimal import Decimal
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
    storefront_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    optimal_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
```

- [ ] **Step 12: Create app/models/price_alerts.py**

```python
import uuid
from datetime import datetime
from sqlalchemy import Enum, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "below_min", "below_optimal", "catalog_mismatch", "quarantine_risk",
            name="alert_type",
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("new", "acked", "resolved", name="alert_status"), nullable=False, default="new"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 13: Create app/models/economics_reports.py**

```python
import uuid
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy import Numeric, Date, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class EconomicsReport(Base):
    __tablename__ = "economics_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    report_source: Mapped[str] = mapped_column(
        Enum("api", "manual_upload", name="report_source"), nullable=False
    )
    services_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sales_commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    boost_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    buyer_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loyalty_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 14: Create app/models/shop_schedule.py**

```python
import uuid
from datetime import datetime
from sqlalchemy import JSON, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class ShopSchedule(Base):
    __tablename__ = "shop_schedule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    standard_schedule: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    override_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_action: Mapped[str | None] = mapped_column(
        Enum("open", "close", name="schedule_action"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 15: Create app/models/events_log.py**

```python
import uuid
from datetime import datetime
from sqlalchemy import String, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class EventLog(Base):
    __tablename__ = "events_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
```

- [ ] **Step 16: Create app/models/__init__.py**

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
from app.models.economics_reports import EconomicsReport
from app.models.shop_schedule import ShopSchedule
from app.models.events_log import EventLog

__all__ = [
    "Base",
    "RawMaterial",
    "MarketProduct",
    "Recipe",
    "Florist",
    "Order",
    "OrderItem",
    "StockMovement",
    "PrintJob",
    "PriceHistory",
    "PriceAlert",
    "EconomicsReport",
    "ShopSchedule",
    "EventLog",
]
```

- [ ] **Step 17: Prepare test database**

```bash
docker compose up -d postgres
docker compose exec postgres createdb -U buds buds_test
```

- [ ] **Step 18: Run tests to verify they pass**

```bash
TEST_DATABASE_URL="postgresql+asyncpg://buds:buds@localhost:5432/buds_test" pytest tests/test_models.py -v
```
Expected: 5 tests PASSED

- [ ] **Step 19: Commit**

```bash
git add app/models/ tests/test_models.py
git commit -m "feat: all 13 SQLAlchemy models matching spec schema"
```

---

### Task 4: Alembic migrations

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/` — populated by autogenerate

- [ ] **Step 1: Initialize Alembic**

```bash
alembic init alembic
```
Expected: creates `alembic.ini` and `alembic/` directory with `env.py`, `script.py.mako`, `versions/`.

- [ ] **Step 2: Replace full contents of alembic/env.py**

```python
import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_url = (
    f"postgresql+asyncpg://{os.environ.get('POSTGRES_USER', 'buds')}:"
    f"{os.environ.get('POSTGRES_PASSWORD', 'buds')}@"
    f"{os.environ.get('POSTGRES_HOST', 'postgres')}:"
    f"{os.environ.get('POSTGRES_PORT', '5432')}/"
    f"{os.environ.get('POSTGRES_DB', 'buds')}"
)
config.set_main_option("sqlalchemy.url", db_url)

from app.models import Base  # noqa: E402
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 3: Generate initial migration**

```bash
docker compose run --rm app alembic revision --autogenerate -m "initial_schema"
```
Expected: new file created in `alembic/versions/` like `xxxx_initial_schema.py`.

- [ ] **Step 4: Apply migration to production database**

```bash
docker compose run --rm app alembic upgrade head
```
Expected:
```
INFO  [alembic.runtime.migration] Running upgrade  -> xxxx, initial_schema
```

- [ ] **Step 5: Verify tables exist**

```bash
docker compose exec postgres psql -U buds -d buds -c "\dt"
```
Expected: 13 tables listed: `economics_reports`, `events_log`, `florists`, `market_products`, `order_items`, `orders`, `price_alerts`, `price_history`, `print_jobs`, `raw_materials`, `recipes`, `shop_schedule`, `stock_movements`.

- [ ] **Step 6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: Alembic async env + initial schema migration for all 13 tables"
```

---

### Task 5: Redis Event Bus

**Files:**
- Create: `app/core/__init__.py`
- Create: `app/core/event_bus.py`
- Test: `tests/test_event_bus.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_event_bus.py`:
```python
import asyncio
import pytest
import pytest_asyncio
import fakeredis.aioredis
from app.core.event_bus import EventBus


@pytest_asyncio.fixture
async def bus():
    fake = fakeredis.aioredis.FakeRedis()
    b = EventBus(fake)
    yield b
    await b.close()


@pytest.mark.asyncio
async def test_publish_and_subscribe(bus):
    received = []

    async def handler(channel: str, data: dict):
        received.append((channel, data))

    await bus.subscribe("order.created", handler)
    await bus.publish("order.created", {"order_id": "YM-001"})
    await asyncio.sleep(0.1)

    assert len(received) == 1
    assert received[0][0] == "order.created"
    assert received[0][1]["order_id"] == "YM-001"


@pytest.mark.asyncio
async def test_multiple_subscribers(bus):
    log_a, log_b = [], []

    await bus.subscribe("order.created", lambda ch, d: log_a.append(d))
    await bus.subscribe("order.created", lambda ch, d: log_b.append(d))
    await bus.publish("order.created", {"order_id": "YM-002"})
    await asyncio.sleep(0.1)

    assert len(log_a) == 1
    assert len(log_b) == 1


@pytest.mark.asyncio
async def test_different_channels_isolated(bus):
    stock_events = []

    await bus.subscribe("stock.updated", lambda ch, d: stock_events.append(d))
    await bus.publish("order.created", {"order_id": "YM-003"})
    await asyncio.sleep(0.1)

    assert stock_events == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_event_bus.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.core.event_bus'`

- [ ] **Step 3: Create app/core/__init__.py** (empty)

- [ ] **Step 4: Create app/core/event_bus.py**

```python
import asyncio
import json
from typing import Callable, Awaitable
from redis.asyncio import Redis

Handler = Callable[[str, dict], Awaitable[None] | None]


class EventBus:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._handlers: dict[str, list[Handler]] = {}
        self._pubsub = None
        self._listener_task: asyncio.Task | None = None

    async def subscribe(self, channel: str, handler: Handler) -> None:
        if channel not in self._handlers:
            self._handlers[channel] = []
        self._handlers[channel].append(handler)

        if self._pubsub is None:
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(channel)
            self._listener_task = asyncio.create_task(self._listen())
        elif channel not in (self._pubsub.channels or {}):
            await self._pubsub.subscribe(channel)

    async def publish(self, channel: str, data: dict) -> None:
        await self._redis.publish(channel, json.dumps(data))

    async def _listen(self) -> None:
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            if isinstance(channel, bytes):
                channel = channel.decode()
            try:
                data = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            for handler in self._handlers.get(channel, []):
                result = handler(channel, data)
                if asyncio.iscoroutine(result):
                    await result

    async def close(self) -> None:
        if self._listener_task:
            self._listener_task.cancel()
        if self._pubsub:
            await self._pubsub.unsubscribe()
            await self._pubsub.aclose()
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_event_bus.py -v
```
Expected: 3 tests PASSED

- [ ] **Step 6: Commit**

```bash
git add app/core/ tests/test_event_bus.py
git commit -m "feat: Redis pub/sub event bus with async handler support"
```

---

### Task 6: FastAPI skeleton + webhook + WebSocket print endpoint

**Files:**
- Create: `app/api/__init__.py`
- Create: `app/api/webhooks.py`
- Create: `app/api/ws_print.py`
- Create: `app/main.py`
- Test: `tests/test_webhooks.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_webhooks.py`:
```python
import pytest
from httpx import AsyncClient, ASGITransport


@pytest.mark.asyncio
async def test_health_endpoint():
    from app.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_market_webhook_accepted():
    from app.main import app
    payload = {
        "type": "ORDER_STATUS_CHANGED",
        "orderId": "YM-123456",
        "status": "PROCESSING",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/market", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["order_id"] == "YM-123456"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_webhooks.py -v
```
Expected: `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Create app/api/__init__.py** (empty)

- [ ] **Step 4: Create app/api/webhooks.py**

```python
from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/market")
async def market_webhook(request: Request):
    payload = await request.json()
    event_type = payload.get("type", "unknown")
    order_id = payload.get("orderId")
    # TODO(order_agent): route to event_bus.publish("order.created", ...) in Order Agent plan
    return {"received": event_type, "order_id": order_id}
```

- [ ] **Step 5: Create app/api/ws_print.py**

```python
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

_print_clients: list[WebSocket] = []


@router.websocket("/ws/print")
async def websocket_print(websocket: WebSocket):
    await websocket.accept()
    _print_clients.append(websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            # TODO(print_agent): handle ACK {"job_id": ..., "status": "done"|"failed"}
            ack = json.loads(raw)
            print(f"Print ACK received: {ack}")
    except WebSocketDisconnect:
        _print_clients.remove(websocket)


async def send_print_job(job: dict) -> bool:
    """Send print job to the first connected print_client. Returns False if no client connected."""
    if not _print_clients:
        return False
    await _print_clients[0].send_text(json.dumps(job))
    return True
```

- [ ] **Step 6: Create app/main.py**

```python
from fastapi import FastAPI
from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router

app = FastAPI(title="BUDS Agent", version="1.0.0")

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/test_webhooks.py -v
```
Expected: 2 tests PASSED

- [ ] **Step 8: Commit**

```bash
git add app/api/ app/main.py tests/test_webhooks.py
git commit -m "feat: FastAPI skeleton with /health, /webhooks/market, and /ws/print"
```

---

### Task 7: aiogram bot skeletons

**Files:**
- Create: `app/bot/__init__.py`
- Create: `app/bot/owner_bot.py`
- Create: `app/bot/florist_bot.py`
- Modify: `app/main.py`

*Bot polling requires live Telegram tokens. No unit tests for this task — verified by starting the app.*

- [ ] **Step 1: Create app/bot/__init__.py** (empty)

- [ ] **Step 2: Create app/bot/owner_bot.py**

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from app.config import settings

owner_router = Router()


@owner_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("BUDS администратор готов к работе.")


@owner_router.message(Command("status"))
async def cmd_status(message: Message):
    await message.answer("Статус: онлайн.")


def create_owner_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher()
    dp.include_router(owner_router)
    return bot, dp
```

- [ ] **Step 3: Create app/bot/florist_bot.py**

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from app.config import settings

florist_router = Router()


@florist_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Флорист подключён. Ожидаю заказы.")


def create_florist_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher()
    dp.include_router(florist_router)
    return bot, dp
```

- [ ] **Step 4: Wire bots into app/main.py lifespan**

Replace full `app/main.py`:
```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router
from app.bot.owner_bot import create_owner_bot
from app.bot.florist_bot import create_florist_bot


@asynccontextmanager
async def lifespan(app: FastAPI):
    owner_bot, owner_dp = create_owner_bot()
    florist_bot, florist_dp = create_florist_bot()
    owner_task = asyncio.create_task(owner_dp.start_polling(owner_bot))
    florist_task = asyncio.create_task(florist_dp.start_polling(florist_bot))
    yield
    owner_task.cancel()
    florist_task.cancel()
    await owner_bot.session.close()
    await florist_bot.session.close()


app = FastAPI(title="BUDS Agent", version="1.0.0", lifespan=lifespan)

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: Verify webhook tests still pass after main.py edit**

```bash
pytest tests/test_webhooks.py -v
```
Expected: 2 tests PASSED

- [ ] **Step 6: Commit**

```bash
git add app/bot/ app/main.py
git commit -m "feat: aiogram owner and florist bot skeletons wired into FastAPI lifespan"
```

---

### Task 8: Phase 0 — Yandex Market API discovery script

**Files:**
- Create: `scripts/test_market_api.py`
- Create: `results/phase0_findings.md`

*Manual script, no unit tests. Run against real API with credentials.*

- [ ] **Step 1: Create scripts/test_market_api.py**

```python
#!/usr/bin/env python3
"""
Phase 0: Yandex Market API capability discovery.

Run with real credentials:
    MARKET_API_TOKEN=... MARKET_CAMPAIGN_ID=... python scripts/test_market_api.py

Optional: ORDER_ID=12345 ... for label check.
"""
import asyncio
import json
import os
import httpx

BASE_URL = "https://api.partner.market.yandex.ru"
TOKEN = os.environ["MARKET_API_TOKEN"]
CAMPAIGN_ID = os.environ["MARKET_CAMPAIGN_ID"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


async def check_order_stats(client: httpx.AsyncClient):
    print("\n=== 1. Financial stats (POST /campaigns/{id}/stats/orders) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/stats/orders"
    r = await client.post(url, json={"dateFrom": "2026-04-01", "dateTo": "2026-04-22"}, headers=HEADERS)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        orders = r.json().get("result", {}).get("orders", [])
        if orders:
            print("Sample order fields:", json.dumps(list(orders[0].keys()), ensure_ascii=False, indent=2))
        else:
            print("No orders in date range — try adjusting dates")
    else:
        print(f"Error: {r.text[:500]}")
    print("CHECK: Are buyerDiscount / boostCost / payments fields present per order?")


async def check_stock_update(client: httpx.AsyncClient):
    print("\n=== 2. Stock update (PUT /campaigns/{id}/offers/stocks) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/offers/stocks"
    payload = {
        "skus": [
            {"sku": "TEST-NONEXISTENT-SKU", "warehouseId": 0, "items": [{"type": "FIT", "count": 0}]}
        ]
    }
    r = await client.put(url, json=payload, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Note the required warehouseId value for your campaign")


async def check_price_update(client: httpx.AsyncClient):
    print("\n=== 3. Price update + quarantine (POST /campaigns/{id}/offer-prices/updates) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/offer-prices/updates"
    payload = {
        "offers": [{"id": "TEST-NONEXISTENT-SKU", "price": {"value": 1, "currencyId": "RUR"}}]
    }
    r = await client.post(url, json=payload, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Which field signals quarantine state? What triggers it?")


async def check_schedule_api(client: httpx.AsyncClient):
    print("\n=== 4. Shop schedule (GET /campaigns/{id}/schedule) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/schedule"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Does GET exist? Does PUT /schedule exist? What is the apply delay?")


async def check_order_label(client: httpx.AsyncClient, order_id: str):
    print("\n=== 5. Order label download ===")
    if not order_id:
        print("SKIP: set ORDER_ID env var to test this check")
        return
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/orders/{order_id}/delivery/labels"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('content-type')}")
    if r.status_code == 200:
        with open("/tmp/test_label.pdf", "wb") as f:
            f.write(r.content)
        print("Label saved to /tmp/test_label.pdf — open and verify it's a valid shipping label")
    else:
        print(f"Error: {r.text[:500]}")


async def check_webhook_settings(client: httpx.AsyncClient):
    print("\n=== 6. Webhook / push notification settings ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/settings"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:1000]}")
    print("CHECK: Is push model configured via API or only via Partner Cabinet?")
    print("CHECK: What fields does a push notification contain for order status changes?")


async def main():
    order_id = os.environ.get("ORDER_ID", "")
    async with httpx.AsyncClient(timeout=30) as client:
        await check_order_stats(client)
        await check_stock_update(client)
        await check_price_update(client)
        await check_schedule_api(client)
        await check_order_label(client, order_id)
        await check_webhook_settings(client)

    print("\n=== Done — fill in results/phase0_findings.md ===")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Create results/phase0_findings.md**

```markdown
# Phase 0: Yandex Market API Findings

Run date: 2026-04-__

## 1. Financial stats (`POST /campaigns/{id}/stats/orders`)
- Has `buyerDiscount` per order: [YES / NO]
- Has `boostCost` per order: [YES / NO]
- Has `salesCommission` per order: [YES / NO]
- Decision: [auto via API / manual CSV upload with `/отчёт` command]

## 2. Stock update (`PUT /campaigns/{id}/offers/stocks`)
- Required `warehouseId`: [value]
- Payload format confirmed: [YES / NO]
- Notes:

## 3. Price update + quarantine
- Quarantine triggered when: [condition, e.g. price drops >X%]
- Quarantine signaled by field: [fieldName in response]
- Notes:

## 4. Schedule API
- `GET /schedule` exists: [YES / NO]
- `PUT /schedule` exists: [YES / NO]
- Delay for changes to apply: [~X hours]
- Notes:

## 5. Order label
- Format: [PDF binary / redirect URL / other]
- Endpoint confirmed working: [YES / NO]
- Notes:

## 6. Webhooks
- Model: [push (Yandex sends to our URL) / pull]
- Configuration: [API / Partner Cabinet only]
- Key fields in push payload for ORDER_STATUS_CHANGED:
  ```json
  {}
  ```

## Decisions
Based on findings:
- Economics data source: [API auto / manual CSV]
- Schedule management: [API / manual]
- Label source: [API download / Partner Cabinet]
```

- [ ] **Step 3: Commit**

```bash
git add scripts/ results/
git commit -m "feat: Phase 0 Market API discovery script + findings template"
```

---

### Task 9: print_client for florist's local PC

**Files:**
- Create: `print_client/requirements.txt`
- Create: `print_client/print_client.py`

*Runs on florist's local machine, not in Docker. No automated tests — verified by connecting to running server manually.*

- [ ] **Step 1: Create print_client/requirements.txt**

```
websockets==13.0
python-escpos==3.1
```

- [ ] **Step 2: Create print_client/print_client.py**

```python
#!/usr/bin/env python3
"""
Runs on the florist's local PC. Connects to BUDS VPS WebSocket,
receives print jobs, prints via ESC/POS thermal printer, sends ACK.

Setup:
    pip install -r print_client/requirements.txt

Run:
    BUDS_WS_URL=ws://YOUR_VPS_IP:8000/ws/print \
    PRINTER_USB_VENDOR=0x04b8 \
    PRINTER_USB_PRODUCT=0x0202 \
    python print_client/print_client.py
"""
import asyncio
import json
import os
import urllib.request
import tempfile

import websockets

BUDS_WS_URL = os.environ.get("BUDS_WS_URL", "ws://localhost:8000/ws/print")
PRINTER_USB_VENDOR = int(os.environ.get("PRINTER_USB_VENDOR", "0x04b8"), 16)
PRINTER_USB_PRODUCT = int(os.environ.get("PRINTER_USB_PRODUCT", "0x0202"), 16)


def print_label(label_url: str, job_id: str) -> bool:
    try:
        from escpos.printer import Usb
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            urllib.request.urlretrieve(label_url, f.name)
        printer = Usb(PRINTER_USB_VENDOR, PRINTER_USB_PRODUCT)
        printer.text(f"Заказ {job_id}\n")
        printer.text(f"{label_url[:60]}\n")
        printer.cut()
        return True
    except Exception as exc:
        print(f"[print_client] Print error for job {job_id}: {exc}")
        return False


async def run():
    print(f"[print_client] Connecting to {BUDS_WS_URL}")
    async for websocket in websockets.connect(BUDS_WS_URL, ping_interval=20):
        try:
            print("[print_client] Connected")
            async for raw in websocket:
                job = json.loads(raw)
                job_id = job.get("job_id", "unknown")
                label_url = job.get("label_url", "")
                print(f"[print_client] Printing job {job_id}")
                success = print_label(label_url, job_id)
                ack = {"job_id": job_id, "status": "done" if success else "failed"}
                await websocket.send(json.dumps(ack))
        except websockets.ConnectionClosed:
            print("[print_client] Disconnected, retrying in 5s...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(run())
```

- [ ] **Step 3: Run full test suite to confirm nothing regressed**

```bash
TEST_DATABASE_URL="postgresql+asyncpg://buds:buds@localhost:5432/buds_test" pytest tests/ -v
```
Expected: all tests PASSED (test_config, test_models, test_event_bus, test_webhooks)

- [ ] **Step 4: Start full Docker Compose stack and verify health**

```bash
docker compose up --build
```
In a separate terminal:
```bash
curl http://localhost:8000/health
```
Expected: `{"status":"ok"}`

- [ ] **Step 5: Commit**

```bash
git add print_client/
git commit -m "feat: print_client WebSocket client for florist's thermal printer"
```

---

## Self-Review vs Spec

| Spec requirement | Task |
|---|---|
| Docker Compose: app + postgres + redis | Task 1 |
| Python 3.11 / FastAPI / aiogram 3.x / SQLAlchemy 2.0 / Alembic / asyncpg / Redis / APScheduler / httpx / Google Sheets API / ESC/POS | Task 1 (requirements.txt) |
| All 13 DB tables from spec schema | Task 3 |
| `available = physical_stock − reserved − 2` | Task 3 (`RawMaterial.available` property) |
| Alembic migrations | Task 4 |
| Redis pub/sub event bus (`event_bus.py`) | Task 5 |
| FastAPI + `POST /webhooks` endpoint | Task 6 |
| WebSocket endpoint for print_client | Task 6 (`/ws/print`) |
| aiogram owner bot + florist bot skeletons | Task 7 |
| Phase 0 Market API testing | Task 8 |
| `print_client.py` for florist's local PC | Task 9 |

**Deferred to agent-specific plans (not in this plan):**
- Order lifecycle, 60-min timers, Telegram inline buttons → Order Agent plan
- Stock reservation, receipt of raw materials, storefront update → Flower Stock Agent plan
- Google Sheets import of 900 SKU + recipes → Flower Stock Agent plan
- Price monitoring every 3h, quarantine alerts → Pricing Agent plan
- LLM-powered command parsing, shop schedule management → Orchestrator plan

**Placeholder scan:** One `# TODO(order_agent)` in `webhooks.py` and one `# TODO(print_agent)` in `ws_print.py` — both are intentional scope markers for the next plans, not unfinished work.

**Type consistency:** `RawMaterial.available` → `Decimal`; `EventBus.subscribe` handler type `Callable[[str, dict], ...]` is consistent between implementation and tests; `send_print_job(job: dict) -> bool` consistent with caller signature in `ws_print.py`.
