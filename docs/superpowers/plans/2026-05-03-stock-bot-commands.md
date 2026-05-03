# Stock Management Bot Commands — Implementation Plan A

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `/add`, `/write_off`, `/inventory`, `/history`, `/report` commands to owner and florist Telegram bots using aiogram 3 FSM.

**Architecture:** 5 new bot modules (FSM + query handlers), 5 new `stock_ops` helpers, 1 Alembic migration (adds `defect` and `inventory_correction` enum values). FSM uses `RedisStorage` for state persistence across restarts. `/add` and `/write_off` registered on both bots; `/inventory` and `/report` owner-only; `/history` on both.

**Tech Stack:** aiogram 3.13.0, `aiogram.fsm.storage.redis.RedisStorage`, SQLAlchemy async, PostgreSQL, Alembic, Redis

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `alembic/versions/001_add_movement_types.py` | Create | Add `defect`, `inventory_correction` to DB enum |
| `app/models/stock_movements.py` | Modify | Add new enum values to Python model |
| `app/agents/flower_stock/stock_ops.py` | Modify | Add 5 helpers |
| `app/bot/add_stock_fsm.py` | Create | `/add` FSM |
| `app/bot/write_off_fsm.py` | Create | `/write_off` FSM |
| `app/bot/inventory_fsm.py` | Create | `/inventory` FSM |
| `app/bot/stock_queries.py` | Create | `/history` + `/report` handlers |
| `app/bot/owner_bot.py` | Modify | Register all new handlers + `/cancel` |
| `app/bot/florist_bot.py` | Modify | Register `/add`, `/write_off`, `/history` + `/cancel` |
| `app/main.py` | Modify | Pass RedisStorage to Dispatchers; update bot commands |
| `tests/agents/flower_stock/test_stock_ops_extended.py` | Create | Tests for new helpers |
| `tests/bot/test_add_stock_fsm.py` | Create | Tests for `/add` FSM logic |
| `tests/bot/test_write_off_fsm.py` | Create | Tests for `/write_off` FSM logic |

---

## Task 1: Alembic migration — add movement types

**Files:**
- Create: `alembic/versions/001_add_movement_types.py`

- [ ] **Step 1: Check alembic.ini exists**

Run: `cat alembic.ini | grep script_location`
Expected: `script_location = alembic`

If the output is empty or file missing, run `alembic init alembic` first.

- [ ] **Step 2: Create migration file**

Create `alembic/versions/001_add_movement_types.py`:

```python
"""add defect and inventory_correction movement types

Revision ID: 001
Revises:
Create Date: 2026-05-03
"""
from alembic import op

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'defect'")
    op.execute("ALTER TYPE movement_type ADD VALUE IF NOT EXISTS 'inventory_correction'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values
    pass
```

- [ ] **Step 3: Apply migration**

If this is the first alembic migration on an existing DB:
```bash
alembic upgrade head
```

If alembic has never been run and the DB already has all tables:
```bash
# First stamp as base so alembic knows the DB exists, then upgrade
alembic stamp base
alembic upgrade head
```

Expected output: `Running upgrade  -> 001, add defect and inventory_correction movement types`

- [ ] **Step 4: Verify in psql**

```bash
psql $DATABASE_URL -c "\dT+ movement_type"
```

Expected: enum listing includes `defect` and `inventory_correction`.

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/001_add_movement_types.py
git commit -m "feat(db): add defect and inventory_correction to movement_type enum"
```

---

## Task 2: Update StockMovement model

**Files:**
- Modify: `app/models/stock_movements.py:19-26`

- [ ] **Step 1: Update the Enum in the model**

In `app/models/stock_movements.py`, replace:

```python
    type: Mapped[str] = mapped_column(
        Enum(
            "arrival", "reserve", "debit", "spoilage", "return", "release", "extra_debit",
            name="movement_type",
        ),
        nullable=False,
    )
```

with:

```python
    type: Mapped[str] = mapped_column(
        Enum(
            "arrival", "reserve", "debit", "spoilage", "return", "release", "extra_debit",
            "defect", "inventory_correction",
            name="movement_type",
        ),
        nullable=False,
    )
```

- [ ] **Step 2: Run existing tests to confirm nothing broke**

```bash
pytest tests/ -v
```

Expected: all existing tests pass.

- [ ] **Step 3: Commit**

```bash
git add app/models/stock_movements.py
git commit -m "feat(model): add defect and inventory_correction to StockMovement enum"
```

---

## Task 3: stock_ops helpers

**Files:**
- Modify: `app/agents/flower_stock/stock_ops.py`
- Create: `tests/agents/flower_stock/test_stock_ops_extended.py`

- [ ] **Step 1: Write failing tests**

Create `tests/agents/flower_stock/test_stock_ops_extended.py`:

```python
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.flower_stock import stock_ops


def _make_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _make_material(physical_stock="50", reserved="0", cost_per_unit="80"):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.name = "Роза 40см"
    m.unit = "шт."
    m.physical_stock = Decimal(physical_stock)
    m.reserved = Decimal(reserved)
    m.cost_per_unit = Decimal(cost_per_unit)
    return m


# ── record_write_off ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_write_off_defect_reduces_stock():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result = await stock_ops.record_write_off(session, mat.id, Decimal("3"), "defect")

    assert mat.physical_stock == Decimal("47")
    added = session.add.call_args[0][0]
    assert added.type == "defect"
    assert added.quantity == Decimal("3")


@pytest.mark.asyncio
async def test_record_write_off_does_not_go_below_zero():
    mat = _make_material(physical_stock="2")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    await stock_ops.record_write_off(session, mat.id, Decimal("10"), "spoilage")

    assert mat.physical_stock == Decimal("0")


# ── record_inventory_correction ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_inventory_correction_positive_delta():
    mat = _make_material(physical_stock="40")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("47"))

    assert mat.physical_stock == Decimal("47")
    assert delta == Decimal("7")
    added = session.add.call_args[0][0]
    assert added.type == "inventory_correction"
    assert added.quantity == Decimal("7")


@pytest.mark.asyncio
async def test_record_inventory_correction_negative_delta():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("43"))

    assert mat.physical_stock == Decimal("43")
    assert delta == Decimal("-7")
    added = session.add.call_args[0][0]
    assert added.quantity == Decimal("7")  # stored as abs


@pytest.mark.asyncio
async def test_record_inventory_correction_no_change_records_nothing():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("50"))

    assert delta == Decimal("0")
    session.add.assert_not_called()


# ── get_recent_orders ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_recent_orders_returns_list():
    session = _make_session()
    orders = [MagicMock(), MagicMock()]
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=orders))))
    )
    result = await stock_ops.get_recent_orders(session, limit=20)
    assert result == orders


# ── get_material_history ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_material_history_returns_list():
    session = _make_session()
    movements = [MagicMock(), MagicMock(), MagicMock()]
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=movements))))
    )
    result = await stock_ops.get_material_history(session, uuid.uuid4(), limit=20)
    assert result == movements


# ── get_report ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_report_sums_correctly():
    from app.models.stock_movements import StockMovement
    from app.models.raw_materials import RawMaterial

    session = _make_session()

    mov1 = MagicMock(); mov1.type = "arrival"; mov1.cost = Decimal("500")
    mov2 = MagicMock(); mov2.type = "spoilage"; mov2.cost = Decimal("80")
    mov3 = MagicMock(); mov3.type = "defect"; mov3.cost = Decimal("40")
    mat1 = MagicMock(); mat1.physical_stock = Decimal("10"); mat1.cost_per_unit = Decimal("100")

    call_count = 0
    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mov1, mov2, mov3]))))
        return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mat1]))))

    session.execute = mock_execute
    since = datetime.now(timezone.utc) - timedelta(days=7)
    report = await stock_ops.get_report(session, since)

    assert report.arrivals_cost == Decimal("500")
    assert report.write_offs_cost == Decimal("120")  # 80 + 40
    assert report.current_stock_value == Decimal("1000")  # 10 * 100
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/agents/flower_stock/test_stock_ops_extended.py -v
```

Expected: FAIL with `AttributeError: module has no attribute 'record_write_off'`

- [ ] **Step 3: Add helpers to stock_ops.py**

At the bottom of `app/agents/flower_stock/stock_ops.py`, add:

```python
from dataclasses import dataclass
from datetime import datetime


async def record_write_off(
    db: AsyncSession, material_id: uuid.UUID, quantity: Decimal, movement_type: str
) -> RawMaterial:
    """Record a manual write-off. movement_type: 'defect' or 'spoilage'."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    cost = quantity * material.cost_per_unit
    material.physical_stock = max(Decimal("0"), material.physical_stock - quantity)
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type=movement_type,
        quantity=quantity,
        cost=cost,
    ))
    await db.commit()
    await db.refresh(material)
    return material


async def record_inventory_correction(
    db: AsyncSession, material_id: uuid.UUID, actual_qty: Decimal
) -> tuple[RawMaterial, Decimal]:
    """Set physical_stock to actual_qty. Returns (material, delta). No-op if no change."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    delta = actual_qty - material.physical_stock
    if delta == Decimal("0"):
        return material, delta
    cost = abs(delta) * material.cost_per_unit
    material.physical_stock = actual_qty
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type="inventory_correction",
        quantity=abs(delta),
        cost=cost,
        note=f"{delta:+.3f}",
    ))
    await db.commit()
    await db.refresh(material)
    return material, delta


async def get_recent_orders(db: AsyncSession, limit: int = 20) -> list[Order]:
    """Return recent orders ordered by created_at DESC."""
    result = await db.execute(
        select(Order).order_by(Order.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_material_history(
    db: AsyncSession, material_id: uuid.UUID, limit: int = 20
) -> list[StockMovement]:
    """Return recent movements for a material, newest first."""
    result = await db.execute(
        select(StockMovement)
        .where(StockMovement.material_id == material_id)
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@dataclass
class ReportData:
    arrivals_cost: Decimal
    write_offs_cost: Decimal
    current_stock_value: Decimal


async def get_report(db: AsyncSession, since: datetime) -> ReportData:
    """Aggregate movements since `since`. Returns cost summary."""
    result = await db.execute(
        select(StockMovement).where(StockMovement.created_at >= since)
    )
    movements = list(result.scalars().all())

    arrivals_cost = sum(
        (m.cost for m in movements if m.type == "arrival"), Decimal("0")
    )
    write_offs_cost = sum(
        (m.cost for m in movements if m.type in ("spoilage", "defect", "extra_debit")),
        Decimal("0"),
    )

    result = await db.execute(select(RawMaterial))
    materials = list(result.scalars().all())
    current_stock_value = sum(
        (m.physical_stock * m.cost_per_unit for m in materials), Decimal("0")
    )

    return ReportData(
        arrivals_cost=arrivals_cost,
        write_offs_cost=write_offs_cost,
        current_stock_value=current_stock_value,
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/agents/flower_stock/test_stock_ops_extended.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add app/agents/flower_stock/stock_ops.py tests/agents/flower_stock/test_stock_ops_extended.py
git commit -m "feat(stock): add write_off, inventory_correction, history and report helpers"
```

---

## Task 4: FSM storage — RedisStorage

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Update imports in main.py**

Add to the import block in `app/main.py`:

```python
from aiogram.fsm.storage.redis import RedisStorage
```

- [ ] **Step 2: Pass RedisStorage to both Dispatchers**

In `main.py`, the `lifespan` function creates `redis` before creating the bots. Update the bot creation section:

Replace:
```python
    owner_bot, owner_dp = create_owner_bot()
```

with:
```python
    redis = Redis.from_url(settings.redis_url)
    event_bus = EventBus(redis)
    app.state.event_bus = event_bus

    fsm_storage = RedisStorage(redis)
    owner_bot, owner_dp = create_owner_bot(fsm_storage)
```

And replace the existing `redis = Redis.from_url(...)` and `event_bus = EventBus(redis)` lines (currently around line 49-51) with just the `fsm_storage` usage (they're already created above now).

The updated lifespan block should look like:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    owner_bot, owner_dp = create_owner_bot()
    await owner_bot.set_my_commands([
        BotCommand(command="stock", description="Остатки склада"),
        BotCommand(command="status", description="Статус бота"),
    ])
    owner_task = asyncio.create_task(owner_dp.start_polling(owner_bot))

    florist_result = create_florist_bot()
    florist_task = None
    florist_bot = None
    if florist_result:
        florist_bot, florist_dp = florist_result
        florist_task = asyncio.create_task(florist_dp.start_polling(florist_bot))

    redis = Redis.from_url(settings.redis_url)
    event_bus = EventBus(redis)
    app.state.event_bus = event_bus
    ...
```

The cleanest approach: move `redis` creation before bot creation, then pass `RedisStorage(redis)` to both `create_owner_bot` and `create_florist_bot`. Replace the full lifespan opening with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = Redis.from_url(settings.redis_url)
    event_bus = EventBus(redis)
    app.state.event_bus = event_bus

    fsm_storage = RedisStorage(redis)
    owner_bot, owner_dp = create_owner_bot(fsm_storage)
    await owner_bot.set_my_commands([
        BotCommand(command="stock", description="Остатки склада"),
        BotCommand(command="add", description="Записать приход"),
        BotCommand(command="write_off", description="Списать материал"),
        BotCommand(command="inventory", description="Инвентаризация"),
        BotCommand(command="history", description="История движений"),
        BotCommand(command="report", description="Отчёт"),
        BotCommand(command="status", description="Статус бота"),
    ])
    owner_task = asyncio.create_task(owner_dp.start_polling(owner_bot))

    florist_result = create_florist_bot(fsm_storage)
    florist_task = None
    florist_bot = None
    if florist_result:
        florist_bot, florist_dp = florist_result
        florist_task = asyncio.create_task(florist_dp.start_polling(florist_bot))

    print_agent = PrintAgent(redis, AsyncSessionLocal, owner_bot, settings)
    ...  # rest unchanged
```

- [ ] **Step 3: Update create_owner_bot and create_florist_bot signatures**

In `app/bot/owner_bot.py`, update:

```python
def create_owner_bot(storage=None) -> tuple[Bot, Dispatcher]:
    from aiogram.fsm.storage.memory import MemoryStorage
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp.include_router(owner_router)
    return bot, dp
```

In `app/bot/florist_bot.py`, update `create_florist_bot` similarly:

```python
def create_florist_bot(storage=None) -> tuple[Bot, Dispatcher] | None:
    if not settings.florist_bot_token:
        return None
    from aiogram.fsm.storage.memory import MemoryStorage
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp.include_router(florist_router)
    return bot, dp
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: all tests PASS (FSM storage doesn't affect existing tests).

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/bot/owner_bot.py app/bot/florist_bot.py
git commit -m "feat(bot): configure RedisStorage for FSM persistence"
```

---

## Task 5: /add FSM

**Files:**
- Create: `app/bot/add_stock_fsm.py`
- Create: `tests/bot/test_add_stock_fsm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/bot/test_add_stock_fsm.py`:

```python
import uuid
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.add_stock_fsm import AddStockStates, _parse_decimal


def test_parse_decimal_comma():
    assert _parse_decimal("10,5") == Decimal("10.5")


def test_parse_decimal_dot():
    assert _parse_decimal("80.00") == Decimal("80.00")


def test_parse_decimal_integer():
    assert _parse_decimal("50") == Decimal("50")


def test_parse_decimal_invalid():
    import pytest
    with pytest.raises(Exception):
        _parse_decimal("abc")


def test_parse_decimal_zero_raises():
    with pytest.raises(ValueError):
        _parse_decimal("0")


def test_parse_decimal_negative_raises():
    with pytest.raises(ValueError):
        _parse_decimal("-5")


@pytest.mark.asyncio
async def test_handle_price_calls_record_arrival():
    material_id = str(uuid.uuid4())
    state = AsyncMock()
    state.get_data = AsyncMock(return_value={
        "material_id": material_id,
        "quantity": "30",
    })
    state.clear = AsyncMock()

    mat = MagicMock()
    mat.name = "Роза"
    mat.unit = "шт."
    mat.physical_stock = Decimal("80")

    message = AsyncMock()
    message.text = "80"
    message.answer = AsyncMock()

    with patch("app.bot.add_stock_fsm.stock_ops") as mock_ops, \
         patch("app.bot.add_stock_fsm.AsyncMock", create=True):
        mock_ops.record_arrival = AsyncMock(return_value=mat)

        db_factory = MagicMock()
        db_session = AsyncMock()
        db_session.__aenter__ = AsyncMock(return_value=db_session)
        db_session.__aexit__ = AsyncMock(return_value=False)
        db_factory.return_value = db_session

        flower_stock_agent = AsyncMock()
        flower_stock_agent._update_storefront = AsyncMock()

        from app.bot.add_stock_fsm import _make_price_handler
        handler = _make_price_handler(db_factory, flower_stock_agent)
        await handler(message, state)

    mock_ops.record_arrival.assert_awaited_once()
    flower_stock_agent._update_storefront.assert_awaited_once()
    message.answer.assert_awaited_once()
    assert "Роза" in message.answer.call_args[0][0]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/bot/test_add_stock_fsm.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.bot.add_stock_fsm'`

- [ ] **Step 3: Create app/bot/add_stock_fsm.py**

```python
import uuid
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)


def _parse_decimal(text: str) -> Decimal:
    d = Decimal(text.replace(",", "."))
    if d <= 0:
        raise ValueError("must be positive")
    return d


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


class AddStockStates(StatesGroup):
    SelectMaterial = State()
    EnterQuantity = State()
    EnterPrice = State()


async def _build_materials_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
        materials = list(result.scalars().all())
    buttons = [
        [InlineKeyboardButton(
            text=f"{m.name} ({_fmt(m.physical_stock)} {m.unit})",
            callback_data=f"add_mat:{m.id}",
        )]
        for m in materials
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _make_price_handler(db_factory: async_sessionmaker, flower_stock_agent):
    async def handle_price(message: Message, state: FSMContext):
        try:
            price = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 80 или 45.50")
            return
        data = await state.get_data()
        material_id = uuid.UUID(data["material_id"])
        qty = Decimal(data["quantity"])
        async with db_factory() as db:
            mat = await stock_ops.record_arrival(db, material_id, qty, price)
        await state.clear()
        await flower_stock_agent._update_storefront()
        await message.answer(
            f"✅ Приход: {_fmt(qty)} {mat.unit} «{mat.name}» по {_fmt(price)}₽\n"
            f"Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
        )
    return handle_price


def register_add_stock_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    @router.message(Command("add"))
    async def cmd_add(message: Message, state: FSMContext):
        keyboard = await _build_materials_keyboard(db_factory)
        await message.answer("Выберите материал:", reply_markup=keyboard)
        await state.set_state(AddStockStates.SelectMaterial)

    @router.callback_query(
        AddStockStates.SelectMaterial,
        lambda c: c.data and c.data.startswith("add_mat:"),
    )
    async def handle_material_selected(callback: CallbackQuery, state: FSMContext):
        material_id_str = callback.data.split(":", 1)[1]
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == material_id_str)
            )
            material = result.scalar_one_or_none()
        if material is None:
            await callback.answer("Материал не найден.")
            return
        await state.update_data(
            material_id=str(material.id),
            material_name=material.name,
            material_unit=material.unit,
        )
        await callback.message.edit_text(
            f"«{material.name}» выбран. Сколько {material.unit}?"
        )
        await state.set_state(AddStockStates.EnterQuantity)
        await callback.answer()

    @router.message(AddStockStates.EnterQuantity, ~F.text.startswith("/"))
    async def handle_quantity(message: Message, state: FSMContext):
        try:
            qty = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 50 или 10.5")
            return
        await state.update_data(quantity=str(qty))
        await message.answer("Цена за единицу (₽)?")
        await state.set_state(AddStockStates.EnterPrice)

    router.message(AddStockStates.EnterPrice, ~F.text.startswith("/"))(
        _make_price_handler(db_factory, flower_stock_agent)
    )
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/bot/test_add_stock_fsm.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot/add_stock_fsm.py tests/bot/test_add_stock_fsm.py
git commit -m "feat(bot): add /add FSM for manual stock arrivals"
```

---

## Task 6: /write_off FSM

**Files:**
- Create: `app/bot/write_off_fsm.py`
- Create: `tests/bot/test_write_off_fsm.py`

- [ ] **Step 1: Write failing tests**

Create `tests/bot/test_write_off_fsm.py`:

```python
import uuid
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.write_off_fsm import WriteOffStates, _TYPE_LABELS


def test_type_labels_has_all_types():
    assert "defect" in _TYPE_LABELS
    assert "spoilage" in _TYPE_LABELS
    assert "extra_debit" in _TYPE_LABELS


@pytest.mark.asyncio
async def test_defect_calls_record_write_off():
    mat = MagicMock()
    mat.name = "Хризантема"
    mat.unit = "шт."
    mat.physical_stock = Decimal("47")

    message = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={
        "wo_type": "defect",
        "material_id": str(uuid.uuid4()),
        "material_name": "Хризантема",
        "material_unit": "шт.",
        "quantity": "3",
    })
    state.clear = AsyncMock()

    db_factory = MagicMock()
    db_session = AsyncMock()
    db_session.__aenter__ = AsyncMock(return_value=db_session)
    db_session.__aexit__ = AsyncMock(return_value=False)
    db_factory.return_value = db_session

    flower_stock_agent = AsyncMock()
    flower_stock_agent._update_storefront = AsyncMock()

    with patch("app.bot.write_off_fsm.stock_ops") as mock_ops:
        mock_ops.record_write_off = AsyncMock(return_value=mat)
        from app.bot.write_off_fsm import _make_complete_handler
        handler = _make_complete_handler(db_factory, flower_stock_agent)
        await handler(message, state)

    mock_ops.record_write_off.assert_awaited_once()
    call_kwargs = mock_ops.record_write_off.call_args
    assert call_kwargs[0][3] == "defect"
    message.answer.assert_awaited_once()
    assert "брак" in message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_extra_debit_calls_record_extra_debit():
    mat = MagicMock()
    mat.name = "Роза"
    mat.unit = "шт."
    mat.physical_stock = Decimal("45")

    message = AsyncMock()
    message.answer = AsyncMock()

    state = AsyncMock()
    state.get_data = AsyncMock(return_value={
        "wo_type": "extra_debit",
        "material_id": str(uuid.uuid4()),
        "material_name": "Роза",
        "material_unit": "шт.",
        "quantity": "2",
        "order_id": str(uuid.uuid4()),
        "market_order_id": "MKT-999",
    })
    state.clear = AsyncMock()

    db_factory = MagicMock()
    db_session = AsyncMock()
    db_session.__aenter__ = AsyncMock(return_value=db_session)
    db_session.__aexit__ = AsyncMock(return_value=False)
    db_factory.return_value = db_session

    flower_stock_agent = AsyncMock()
    flower_stock_agent._update_storefront = AsyncMock()

    with patch("app.bot.write_off_fsm.stock_ops") as mock_ops:
        mock_ops.record_extra_debit = AsyncMock(return_value=mat)
        from app.bot.write_off_fsm import _make_complete_handler
        handler = _make_complete_handler(db_factory, flower_stock_agent)
        await handler(message, state)

    mock_ops.record_extra_debit.assert_awaited_once()
    assert "MKT-999" in message.answer.call_args[0][0]
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/bot/test_write_off_fsm.py -v
```

Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create app/bot/write_off_fsm.py**

```python
import uuid
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)

_TYPE_LABELS = {
    "defect": "брак",
    "spoilage": "порча",
    "extra_debit": "к заказу",
}

_TYPE_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🪲 Брак", callback_data="wo_type:defect"),
        InlineKeyboardButton(text="🌿 Порча", callback_data="wo_type:spoilage"),
        InlineKeyboardButton(text="📦 К заказу", callback_data="wo_type:extra_debit"),
    ]
])


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _parse_decimal(text: str) -> Decimal:
    d = Decimal(text.replace(",", "."))
    if d <= 0:
        raise ValueError("must be positive")
    return d


class WriteOffStates(StatesGroup):
    SelectType = State()
    SelectMaterial = State()
    EnterQuantity = State()
    SelectOrder = State()


async def _build_materials_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
        materials = list(result.scalars().all())
    buttons = [
        [InlineKeyboardButton(
            text=f"{m.name} ({_fmt(m.physical_stock)} {m.unit})",
            callback_data=f"wo_mat:{m.id}",
        )]
        for m in materials
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_orders_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        orders = await stock_ops.get_recent_orders(db, limit=20)
    if not orders:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет заказов", callback_data="wo_order:none:none")]
        ])
    buttons = [
        [InlineKeyboardButton(
            text=f"#{o.market_order_id}",
            callback_data=f"wo_order:{o.id}:{o.market_order_id}",
        )]
        for o in orders
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _make_complete_handler(db_factory: async_sessionmaker, flower_stock_agent):
    async def complete_write_off(message: Message, state: FSMContext):
        data = await state.get_data()
        material_id = uuid.UUID(data["material_id"])
        qty = Decimal(data["quantity"])
        wo_type = data["wo_type"]

        async with db_factory() as db:
            if wo_type == "extra_debit":
                order_id = uuid.UUID(data["order_id"])
                market_order_id = data["market_order_id"]
                mat = await stock_ops.record_extra_debit(
                    db, material_id, order_id, qty,
                    note=f"доп. списание к заказу #{market_order_id}",
                )
                label = f"к заказу #{market_order_id}"
            else:
                mat = await stock_ops.record_write_off(db, material_id, qty, wo_type)
                label = _TYPE_LABELS[wo_type]

        await state.clear()
        await flower_stock_agent._update_storefront()
        await message.answer(
            f"✅ Списано: {_fmt(qty)} {mat.unit} «{mat.name}» ({label})\n"
            f"Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
        )
    return complete_write_off


def register_write_off_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    complete_handler = _make_complete_handler(db_factory, flower_stock_agent)

    @router.message(Command("write_off"))
    async def cmd_write_off(message: Message, state: FSMContext):
        await message.answer("Тип списания:", reply_markup=_TYPE_KEYBOARD)
        await state.set_state(WriteOffStates.SelectType)

    @router.callback_query(
        WriteOffStates.SelectType,
        lambda c: c.data and c.data.startswith("wo_type:"),
    )
    async def handle_type_selected(callback: CallbackQuery, state: FSMContext):
        wo_type = callback.data.split(":", 1)[1]
        await state.update_data(wo_type=wo_type)
        keyboard = await _build_materials_keyboard(db_factory)
        await callback.message.edit_text("Выберите материал:", reply_markup=keyboard)
        await state.set_state(WriteOffStates.SelectMaterial)
        await callback.answer()

    @router.callback_query(
        WriteOffStates.SelectMaterial,
        lambda c: c.data and c.data.startswith("wo_mat:"),
    )
    async def handle_material_selected(callback: CallbackQuery, state: FSMContext):
        material_id_str = callback.data.split(":", 1)[1]
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == material_id_str)
            )
            material = result.scalar_one_or_none()
        if material is None:
            await callback.answer("Материал не найден.")
            return
        await state.update_data(
            material_id=str(material.id),
            material_name=material.name,
            material_unit=material.unit,
        )
        await callback.message.edit_text(
            f"«{material.name}» выбран. Сколько {material.unit}?"
        )
        await state.set_state(WriteOffStates.EnterQuantity)
        await callback.answer()

    @router.message(WriteOffStates.EnterQuantity, ~F.text.startswith("/"))
    async def handle_quantity(message: Message, state: FSMContext):
        try:
            qty = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 3 или 1.5")
            return
        data = await state.get_data()
        await state.update_data(quantity=str(qty))
        if data["wo_type"] == "extra_debit":
            keyboard = await _build_orders_keyboard(db_factory)
            await message.answer("Выберите заказ:", reply_markup=keyboard)
            await state.set_state(WriteOffStates.SelectOrder)
        else:
            await complete_handler(message, state)

    @router.callback_query(
        WriteOffStates.SelectOrder,
        lambda c: c.data and c.data.startswith("wo_order:"),
    )
    async def handle_order_selected(callback: CallbackQuery, state: FSMContext):
        parts = callback.data.split(":", 2)
        if parts[1] == "none":
            await callback.answer("Нет доступных заказов.")
            return
        await state.update_data(order_id=parts[1], market_order_id=parts[2])
        await callback.answer()
        await complete_handler(callback.message, state)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/bot/test_write_off_fsm.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add app/bot/write_off_fsm.py tests/bot/test_write_off_fsm.py
git commit -m "feat(bot): add /write_off FSM (defect, spoilage, extra_debit)"
```

---

## Task 7: /inventory FSM

**Files:**
- Create: `app/bot/inventory_fsm.py`

- [ ] **Step 1: Create app/bot/inventory_fsm.py**

```python
import uuid
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


class InventoryStates(StatesGroup):
    AuditMaterial = State()


async def _ask_next(message: Message, state: FSMContext, db_factory: async_sessionmaker):
    """Advance to the next material or finish the audit."""
    data = await state.get_data()
    material_ids: list[str] = data["material_ids"]
    index: int = data["index"]

    while index < len(material_ids):
        mid = uuid.UUID(material_ids[index])
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == mid)
            )
            material = result.scalar_one_or_none()

        if material is None:
            index += 1
            await state.update_data(index=index)
            continue

        if material.reserved > 0:
            await message.answer(
                f"⚠️ «{material.name}» — в резерве {_fmt(material.reserved)} {material.unit}, пропускаю."
            )
            index += 1
            await state.update_data(index=index)
            continue

        await message.answer(
            f"📦 {material.name}: в системе {_fmt(material.physical_stock)} {material.unit}.\n"
            f"Сколько по факту? (или /skip чтобы пропустить)"
        )
        return

    corrections = data.get("corrections", 0)
    await state.clear()
    await message.answer(
        f"✅ Инвентаризация завершена. Исправлено {corrections} позиций."
    )


def register_inventory_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    @router.message(Command("inventory"))
    async def cmd_inventory(message: Message, state: FSMContext):
        async with db_factory() as db:
            result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
            materials = list(result.scalars().all())
        if not materials:
            await message.answer("Склад пуст.")
            return
        await state.set_data({
            "material_ids": [str(m.id) for m in materials],
            "index": 0,
            "corrections": 0,
        })
        await state.set_state(InventoryStates.AuditMaterial)
        await _ask_next(message, state, db_factory)

    @router.message(InventoryStates.AuditMaterial, Command("skip"))
    async def handle_skip(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.update_data(index=data["index"] + 1)
        await _ask_next(message, state, db_factory)

    @router.message(InventoryStates.AuditMaterial, ~F.text.startswith("/"))
    async def handle_count(message: Message, state: FSMContext):
        try:
            actual = Decimal((message.text or "").replace(",", "."))
            if actual < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            await message.answer("Введите число ≥ 0, например: 47 или 10.5")
            return
        data = await state.get_data()
        mid = uuid.UUID(data["material_ids"][data["index"]])
        async with db_factory() as db:
            mat, delta = await stock_ops.record_inventory_correction(db, mid, actual)
        corrections = data.get("corrections", 0)
        if delta != Decimal("0"):
            sign = "+" if delta > 0 else ""
            await message.answer(f"✏️ {mat.name}: {sign}{_fmt(delta)} {mat.unit}")
            corrections += 1
        await state.update_data(index=data["index"] + 1, corrections=corrections)
        await _ask_next(message, state, db_factory)
        await flower_stock_agent._update_storefront()
```

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all existing tests PASS (no tests yet for inventory — it's simpler to verify manually).

- [ ] **Step 3: Commit**

```bash
git add app/bot/inventory_fsm.py
git commit -m "feat(bot): add /inventory FSM for periodic stock audit"
```

---

## Task 8: /history and /report handlers

**Files:**
- Create: `app/bot/stock_queries.py`

- [ ] **Step 1: Create app/bot/stock_queries.py**

```python
import uuid
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)

_MOVEMENT_LABELS = {
    "arrival": "Приход",
    "reserve": "Резерв",
    "debit": "Списание (заказ)",
    "release": "Снятие резерва",
    "spoilage": "Порча",
    "defect": "Брак",
    "extra_debit": "Доп. списание",
    "inventory_correction": "Инвентаризация",
    "return": "Возврат",
}

_REPORT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="За сегодня", callback_data="report:today"),
        InlineKeyboardButton(text="За неделю", callback_data="report:week"),
        InlineKeyboardButton(text="За месяц", callback_data="report:month"),
    ]
])


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def register_stock_query_handlers(router: Router, db_factory: async_sessionmaker) -> None:
    @router.message(Command("history"))
    async def cmd_history(message: Message):
        async with db_factory() as db:
            result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
            materials = list(result.scalars().all())
        if not materials:
            await message.answer("Склад пуст.")
            return
        buttons = [
            [InlineKeyboardButton(text=m.name, callback_data=f"hist_mat:{m.id}")]
            for m in materials
        ]
        await message.answer(
            "Выберите материал:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("hist_mat:"))
    async def handle_history_material(callback: CallbackQuery):
        material_id = uuid.UUID(callback.data.split(":", 1)[1])
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == material_id)
            )
            material = result.scalar_one_or_none()
            movements = await stock_ops.get_material_history(db, material_id)
        if material is None:
            await callback.answer("Материал не найден.")
            return
        if not movements:
            await callback.message.edit_text(f"«{material.name}» — нет движений.")
            await callback.answer()
            return
        lines = [f"📋 История: «{material.name}»\n"]
        for m in movements:
            ts = m.created_at.strftime("%d.%m %H:%M")
            label = _MOVEMENT_LABELS.get(m.type, m.type)
            lines.append(f"{ts} · {label} {_fmt(m.quantity)} {material.unit}")
        await callback.message.edit_text("\n".join(lines))
        await callback.answer()

    @router.message(Command("report"))
    async def cmd_report(message: Message):
        await message.answer("Выберите период:", reply_markup=_REPORT_KEYBOARD)

    @router.callback_query(lambda c: c.data and c.data.startswith("report:"))
    async def handle_report_period(callback: CallbackQuery):
        period = callback.data.split(":", 1)[1]
        now = datetime.now(timezone.utc)
        if period == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            label = "сегодня"
        elif period == "week":
            since = now - timedelta(days=7)
            label = "7 дней"
        else:
            since = now - timedelta(days=30)
            label = "30 дней"
        async with db_factory() as db:
            report = await stock_ops.get_report(db, since)
        text = (
            f"📊 Отчёт за {label}\n\n"
            f"Закупки: {_fmt(report.arrivals_cost)}₽\n"
            f"Списания: {_fmt(report.write_offs_cost)}₽\n"
            f"Стоимость склада: {_fmt(report.current_stock_value)}₽"
        )
        await callback.message.edit_text(text)
        await callback.answer()
```

- [ ] **Step 2: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add app/bot/stock_queries.py
git commit -m "feat(bot): add /history and /report handlers"
```

---

## Task 9: Wire handlers + update bot commands

**Files:**
- Modify: `app/bot/owner_bot.py`
- Modify: `app/bot/florist_bot.py`
- Modify: `app/main.py`

- [ ] **Step 1: Fix catch-all handler in owner_bot.py (critical)**

The existing `handle_stock_message` has no state filter and will intercept all FSM text inputs if left as-is.

In `app/bot/owner_bot.py`, add import at the top:

```python
from aiogram import F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
```

Then in `register_stock_commands`, replace:

```python
    @owner_router.message()
    async def handle_stock_message(message: Message):
```

with:

```python
    @owner_router.message(StateFilter(default_state))
    async def handle_stock_message(message: Message):
```

This ensures the catch-all only runs when no FSM state is active.

- [ ] **Step 2: Update owner_bot.py — add register functions**

Add register functions at the bottom of `app/bot/owner_bot.py`:

```python
def register_add_handlers(flower_stock_agent, db_factory) -> None:
    from app.bot.add_stock_fsm import register_add_stock_handlers
    register_add_stock_handlers(owner_router, db_factory, flower_stock_agent)


def register_write_off_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.write_off_fsm import register_write_off_handlers
    register_write_off_handlers(owner_router, db_factory, flower_stock_agent)


def register_inventory_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.inventory_fsm import register_inventory_handlers
    register_inventory_handlers(owner_router, db_factory, flower_stock_agent)


def register_query_handlers(db_factory) -> None:
    from app.bot.stock_queries import register_stock_query_handlers
    register_stock_query_handlers(owner_router, db_factory)


def register_cancel_handler() -> None:
    from aiogram.fsm.context import FSMContext

    @owner_router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        current = await state.get_state()
        if current is not None:
            await state.clear()
            await message.answer("Отменено.")
```

- [ ] **Step 2: Update florist_bot.py**

Add register functions for florist-accessible commands at the bottom of `app/bot/florist_bot.py`:

```python
def register_add_handlers(flower_stock_agent, db_factory) -> None:
    from app.bot.add_stock_fsm import register_add_stock_handlers
    register_add_stock_handlers(florist_router, db_factory, flower_stock_agent)


def register_write_off_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.write_off_fsm import register_write_off_handlers
    register_write_off_handlers(florist_router, db_factory, flower_stock_agent)


def register_query_handlers(db_factory) -> None:
    from app.bot.stock_queries import register_stock_query_handlers
    register_stock_query_handlers(florist_router, db_factory)


def register_cancel_handler() -> None:
    from aiogram.fsm.context import FSMContext

    @florist_router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        current = await state.get_state()
        if current is not None:
            await state.clear()
            await message.answer("Отменено.")
```

- [ ] **Step 3: Update main.py imports**

Add to existing imports in `app/main.py`:

```python
from app.bot.owner_bot import (
    create_owner_bot,
    register_order_callbacks as register_owner_callbacks,
    register_stock_commands,
    register_pricing_callbacks,
    register_eucalyptus_callbacks as register_owner_eucalyptus_callbacks,
    register_add_handlers as register_owner_add,
    register_write_off_handler as register_owner_write_off,
    register_inventory_handler as register_owner_inventory,
    register_query_handlers as register_owner_queries,
    register_cancel_handler as register_owner_cancel,
)
from app.bot.florist_bot import (
    create_florist_bot,
    register_order_callbacks as register_florist_callbacks,
    register_eucalyptus_callbacks as register_florist_eucalyptus_callbacks,
    register_add_handlers as register_florist_add,
    register_write_off_handler as register_florist_write_off,
    register_query_handlers as register_florist_queries,
    register_cancel_handler as register_florist_cancel,
)
```

- [ ] **Step 4: Call register functions in lifespan**

In `app/main.py`, after the existing register calls (after `register_owner_eucalyptus_callbacks` and `register_florist_eucalyptus_callbacks`), add:

```python
    register_owner_cancel()
    register_owner_add(flower_stock_agent, AsyncSessionLocal)
    register_owner_write_off(flower_stock_agent, AsyncSessionLocal)
    register_owner_inventory(flower_stock_agent, AsyncSessionLocal)
    register_owner_queries(AsyncSessionLocal)

    if florist_bot:
        register_florist_cancel()
        register_florist_add(flower_stock_agent, AsyncSessionLocal)
        register_florist_write_off(flower_stock_agent, AsyncSessionLocal)
        register_florist_queries(AsyncSessionLocal)
```

- [ ] **Step 5: Update owner bot commands**

In the `set_my_commands` call in `main.py`, replace the current list:

```python
    await owner_bot.set_my_commands([
        BotCommand(command="stock", description="Остатки склада"),
        BotCommand(command="add", description="Записать приход"),
        BotCommand(command="write_off", description="Списать материал"),
        BotCommand(command="inventory", description="Инвентаризация"),
        BotCommand(command="history", description="История движений"),
        BotCommand(command="report", description="Отчёт за период"),
        BotCommand(command="cancel", description="Отменить текущее действие"),
        BotCommand(command="status", description="Статус бота"),
    ])
```

- [ ] **Step 6: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add app/bot/owner_bot.py app/bot/florist_bot.py app/main.py
git commit -m "feat(bot): wire /add, /write_off, /inventory, /history, /report commands"
```

---

## Task 10: Deploy and smoke test

- [ ] **Step 1: Push to remote**

```bash
git push
```

- [ ] **Step 2: Pull on VPS and restart**

```bash
ssh root@nl-vmpico "cd ~/buds-agent && git pull && docker compose restart app"
```

- [ ] **Step 3: Apply migration on VPS**

```bash
ssh root@nl-vmpico "cd ~/buds-agent && docker compose exec app alembic upgrade head"
```

Expected: `Running upgrade  -> 001, add defect and inventory_correction movement types`

- [ ] **Step 4: Smoke test in Telegram**

Send to owner bot:
1. `/add` → tap a material → type quantity → type price → confirm ✅ Приход
2. `/write_off` → tap Брак → tap a material → type qty → confirm ✅ Списано (брак)
3. `/write_off` → tap К заказу → tap a material → type qty → tap an order → confirm
4. `/inventory` → enter counts for 2 materials → `/skip` one → ✅ Инвентаризация завершена
5. `/history` → tap a material → see movement list
6. `/report` → tap За неделю → see cost summary
7. `/cancel` mid-flow → Отменено

---

## Notes for Plan B (Invoice Scanning)

Plan B adds:
- `supplier_aliases` DB table
- `app/agents/flower_stock/invoice_reader.py` (Claude Haiku vision + openpyxl)
- `app/agents/flower_stock/synonym_ops.py`
- `app/bot/scan_invoice.py` (photo + xlsx handler with per-item confirmation loop)
- New deps: `anthropic`, `openpyxl` in `requirements.txt`

Plan B can be implemented independently after Plan A is live.
