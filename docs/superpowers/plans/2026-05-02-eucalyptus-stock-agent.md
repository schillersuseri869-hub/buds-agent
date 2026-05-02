# Eucalyptus Stock Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a `-e` bouquet order comes in and eucalyptus stock drops below 200g net, automatically alert the florist and owner via Telegram with restock buttons; restore storefront when they respond.

**Architecture:** Triggered on `order.created` inside `FlowerStockAgent.handle_order_created`. Stock hiding is implicit (existing `_update_storefront` recalculates all SKUs). New `_alert_all` method sends to both bots. Callback handler `handle_eucalyptus_callback` sets absolute stock and restores storefront.

**Tech Stack:** Python 3.11, aiogram 3.x, SQLAlchemy async, pytest-asyncio, `unittest.mock`

---

## File Map

| File | Change |
|------|--------|
| `app/agents/flower_stock/stock_ops.py` | Add `is_eucalyptus_low`, `set_eucalyptus_stock` |
| `app/agents/flower_stock/agent.py` | Add `florist_bot` param, `_EVKALIPT_KEYBOARD`, `_alert_all`, `handle_eucalyptus_callback`; extend `handle_order_created` |
| `app/bot/owner_bot.py` | Add `register_eucalyptus_callbacks` |
| `app/bot/florist_bot.py` | Add `register_eucalyptus_callbacks` |
| `app/main.py` | Pass `florist_bot` to agent; register eucalyptus callbacks for both bots |
| `tests/agents/flower_stock/test_stock_ops.py` | Add tests for two new functions |
| `tests/agents/flower_stock/test_agent.py` | Update `_make_agent`; add tests for new methods |

---

### Task 1: stock_ops — eucalyptus helpers

**Files:**
- Modify: `app/agents/flower_stock/stock_ops.py`
- Test: `tests/agents/flower_stock/test_stock_ops.py`

- [ ] **Step 1.1: Write failing tests**

Append to `tests/agents/flower_stock/test_stock_ops.py`:

```python
from app.agents.flower_stock.stock_ops import (
    # existing imports stay — add:
    is_eucalyptus_low,
    set_eucalyptus_stock,
)


# ─── is_eucalyptus_low ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_false_when_not_found(db_session):
    # No "evkalipt" row in DB at all
    result = await is_eucalyptus_low(db_session)
    assert result is False


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_false_when_sufficient(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("500"), reserved=Decimal("100"), cost=Decimal("1"))
    # 500 - 100 = 400 >= 200 → not low
    result = await is_eucalyptus_low(db_session)
    assert result is False


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_true_below_200(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("300"), reserved=Decimal("150"), cost=Decimal("1"))
    # 300 - 150 = 150 < 200 → low
    result = await is_eucalyptus_low(db_session)
    assert result is True


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_true_at_zero(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("0"), reserved=Decimal("0"), cost=Decimal("1"))
    result = await is_eucalyptus_low(db_session)
    assert result is True


# ─── set_eucalyptus_stock ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_eucalyptus_stock_sets_absolute_value(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("0"), cost=Decimal("5"))

    updated = await set_eucalyptus_stock(db_session, Decimal("400"))

    assert updated.physical_stock == Decimal("400")


@pytest.mark.asyncio
async def test_set_eucalyptus_stock_overwrites_previous_value(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("600"), cost=Decimal("5"))

    updated = await set_eucalyptus_stock(db_session, Decimal("200"))

    assert updated.physical_stock == Decimal("200")


@pytest.mark.asyncio
async def test_set_eucalyptus_stock_creates_arrival_movement(db_session):
    mat = await _mat(db_session, name="evkalipt", type_="flower", unit="г",
                     physical=Decimal("0"), cost=Decimal("5"))

    await set_eucalyptus_stock(db_session, Decimal("400"))

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.material_id == mat.id,
            StockMovement.type == "arrival",
        )
    )
    movements = list(result.scalars().all())
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("400")
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
pytest tests/agents/flower_stock/test_stock_ops.py -k "eucalyptus" -v
```

Expected: `ImportError: cannot import name 'is_eucalyptus_low'`

- [ ] **Step 1.3: Implement the two functions**

Append to `app/agents/flower_stock/stock_ops.py`:

```python
async def is_eucalyptus_low(db: AsyncSession) -> bool:
    """True if net eucalyptus (physical_stock - reserved) is below 200g."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.name == "evkalipt")
    )
    mat = result.scalar_one_or_none()
    if mat is None:
        return False
    return (mat.physical_stock - mat.reserved) < Decimal("200")


async def set_eucalyptus_stock(db: AsyncSession, quantity: Decimal) -> RawMaterial:
    """Set eucalyptus physical_stock to an absolute value (florist's count).
    Logs a StockMovement type='arrival' with the reported total (manual correction)."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.name == "evkalipt").with_for_update()
    )
    mat = result.scalar_one()
    mat.physical_stock = quantity
    db.add(StockMovement(
        material_id=mat.id,
        order_id=None,
        type="arrival",
        quantity=quantity,
        cost=quantity * mat.cost_per_unit,
    ))
    await db.commit()
    await db.refresh(mat)
    return mat
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
pytest tests/agents/flower_stock/test_stock_ops.py -k "eucalyptus" -v
```

Expected: 7 tests PASSED

- [ ] **Step 1.5: Commit**

```bash
git add app/agents/flower_stock/stock_ops.py tests/agents/flower_stock/test_stock_ops.py
git commit -m "feat(stock): add is_eucalyptus_low and set_eucalyptus_stock"
```

---

### Task 2: agent.py — florist_bot, keyboard, alert, callback, order check

**Files:**
- Modify: `app/agents/flower_stock/agent.py`
- Test: `tests/agents/flower_stock/test_agent.py`

- [ ] **Step 2.1: Write failing tests**

Replace the `_make_agent` helper and append new tests in `tests/agents/flower_stock/test_agent.py`:

```python
# ── update _make_agent to accept florist_bot ─────────────────────────────────
# Replace the existing _make_agent function (lines 9-24) with:

def _make_agent(db_factory=None, owner_bot=None, settings=None, florist_bot=None):
    if db_factory is None:
        session_mock = AsyncMock()
        session_mock.__aenter__ = AsyncMock(return_value=session_mock)
        session_mock.__aexit__ = AsyncMock(return_value=False)
        db_factory = MagicMock(return_value=session_mock)
    if owner_bot is None:
        owner_bot = AsyncMock()
        owner_bot.send_message = AsyncMock()
    if settings is None:
        settings = MagicMock()
        settings.owner_telegram_id = 111111
        settings.florist_telegram_id = 222222
        settings.market_campaign_id = 148807227
        settings.market_api_token = "test_token"
        settings.market_warehouse_id = 99
    return FlowerStockAgent(db_factory, owner_bot, settings, florist_bot=florist_bot)
```

Append these tests at the end of `tests/agents/flower_stock/test_agent.py`:

```python
# ─── handle_eucalyptus_callback ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_eucalyptus_callback_zero_does_nothing():
    agent = _make_agent()
    with patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_ops.set_eucalyptus_stock = AsyncMock()
        await agent.handle_eucalyptus_callback(0)
    mock_ops.set_eucalyptus_stock.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_eucalyptus_callback_sets_stock_and_restores_storefront():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = _make_agent(owner_bot=owner_bot)

    with patch("app.agents.flower_stock.agent.stock_ops") as mock_ops, \
         patch("app.agents.flower_stock.agent.market_api") as mock_mapi:
        mock_ops.set_eucalyptus_stock = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={"SKU-e": 3})
        mock_mapi.update_stocks = AsyncMock()

        await agent.handle_eucalyptus_callback(400)

    mock_ops.set_eucalyptus_stock.assert_awaited_once()
    mock_mapi.update_stocks.assert_awaited_once()
    owner_bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_handle_eucalyptus_callback_alerts_both_bots():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    florist_bot = AsyncMock()
    florist_bot.send_message = AsyncMock()
    settings = MagicMock()
    settings.owner_telegram_id = 111111
    settings.florist_telegram_id = 222222
    settings.market_campaign_id = 1
    settings.market_api_token = "t"
    settings.market_warehouse_id = 0
    agent = _make_agent(owner_bot=owner_bot, settings=settings, florist_bot=florist_bot)

    with patch("app.agents.flower_stock.agent.stock_ops") as mock_ops, \
         patch("app.agents.flower_stock.agent.market_api") as mock_mapi:
        mock_ops.set_eucalyptus_stock = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_mapi.update_stocks = AsyncMock()

        await agent.handle_eucalyptus_callback(200)

    owner_bot.send_message.assert_awaited()
    florist_bot.send_message.assert_awaited()


# ─── eucalyptus check in handle_order_created ───────────────────────────────

@pytest.mark.asyncio
async def test_handle_order_created_sends_alert_when_eucalyptus_low():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    fake_items = [{"sku": "bouquet-e-red", "count": 1, "price": 500}]

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi, \
         patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_mapi.get_order_items = AsyncMock(return_value=fake_items)
        mock_ops.save_order_items = AsyncMock()
        mock_ops.reserve_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_ops.is_eucalyptus_low = AsyncMock(return_value=True)
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent(owner_bot=owner_bot)
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "MKT-E-001",
        })

    sent_texts = [str(call) for call in owner_bot.send_message.call_args_list]
    assert any("Эвкалипт" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_handle_order_created_no_alert_when_eucalyptus_ok():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    fake_items = [{"sku": "bouquet-e-red", "count": 1, "price": 500}]

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi, \
         patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_mapi.get_order_items = AsyncMock(return_value=fake_items)
        mock_ops.save_order_items = AsyncMock()
        mock_ops.reserve_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_ops.is_eucalyptus_low = AsyncMock(return_value=False)
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent(owner_bot=owner_bot)
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "MKT-E-002",
        })

    sent_texts = [str(call) for call in owner_bot.send_message.call_args_list]
    assert not any("Эвкалипт" in t for t in sent_texts)


@pytest.mark.asyncio
async def test_handle_order_created_no_alert_when_no_e_sku():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    fake_items = [{"sku": "bouquet-classic", "count": 1, "price": 500}]

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi, \
         patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_mapi.get_order_items = AsyncMock(return_value=fake_items)
        mock_ops.save_order_items = AsyncMock()
        mock_ops.reserve_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_ops.is_eucalyptus_low = AsyncMock(return_value=True)
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent(owner_bot=owner_bot)
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "MKT-NOEUC-001",
        })

    mock_ops.is_eucalyptus_low.assert_not_awaited()
```

- [ ] **Step 2.2: Run tests to verify they fail**

```bash
pytest tests/agents/flower_stock/test_agent.py -k "eucalyptus or florist_bot" -v
```

Expected: errors — `FlowerStockAgent.__init__() got unexpected keyword argument 'florist_bot'`

- [ ] **Step 2.3: Implement changes in agent.py**

The full updated `app/agents/flower_stock/agent.py`:

```python
import logging
import re
import uuid
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.orders import Order
from app.agents.flower_stock import market_api, stock_ops

logger = logging.getLogger(__name__)

_ARRIVAL_RE = re.compile(
    r"пришло\s+(\d+(?:[.,]\d+)?)\s+(.+?)\s+по\s+(\d+(?:[.,]\d+)?)₽?",
    re.IGNORECASE,
)
_EXTRA_DEBIT_RE = re.compile(
    r"дополнительно\s+списал\s+(\d+(?:[.,]\d+)?)\s+(.+?)\s+к\s+заказу\s+#(\S+)",
    re.IGNORECASE,
)
_SPOILAGE_RE = re.compile(
    r"^списал\s+(\d+(?:[.,]\d+)?)\s+(.+)",
    re.IGNORECASE,
)

_EVKALIPT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="200г", callback_data="evk_restock:200"),
        InlineKeyboardButton(text="400г", callback_data="evk_restock:400"),
        InlineKeyboardButton(text="600г", callback_data="evk_restock:600"),
    ],
    [InlineKeyboardButton(text="Не добавлять", callback_data="evk_restock:0")],
])


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", "."))


class FlowerStockAgent:
    def __init__(
        self,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
        florist_bot: Bot | None = None,
    ):
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings
        self._florist_bot = florist_bot

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, message)
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)

    async def _alert_all(self, message: str, reply_markup=None) -> None:
        """Send message to owner and florist (if configured)."""
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, message, reply_markup=reply_markup
            )
        except Exception as exc:
            logger.error("Failed to send owner alert: %s", exc)
        if self._florist_bot and self._settings.florist_telegram_id:
            try:
                await self._florist_bot.send_message(
                    self._settings.florist_telegram_id, message, reply_markup=reply_markup
                )
            except Exception as exc:
                logger.error("Failed to send florist alert: %s", exc)

    async def _update_storefront(self) -> None:
        try:
            async with self._db_factory() as db:
                stocks = await stock_ops.compute_available_stocks(db)
            await market_api.update_stocks(
                self._settings.market_campaign_id,
                self._settings.market_api_token,
                self._settings.market_warehouse_id,
                stocks,
            )
        except Exception as exc:
            logger.error("_update_storefront failed: %s", exc)
            await self._alert(f"Ошибка обновления витрины Маркета: {exc}")

    async def handle_eucalyptus_callback(self, qty_g: int) -> None:
        """Handle florist/owner restock button tap. qty_g=0 means 'do not restock'."""
        if qty_g == 0:
            return
        async with self._db_factory() as db:
            mat = await stock_ops.set_eucalyptus_stock(db, Decimal(qty_g))
        await self._update_storefront()
        await self._alert_all(
            f"✅ Эвкалипт: {qty_g}г. Позиции с эвкалиптом возвращены на витрину."
        )

    async def handle_order_created(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        market_order_id = data.get("market_order_id")
        if not order_id_str or not market_order_id:
            logger.error("order.created missing fields: %s", data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        try:
            items = await market_api.get_order_items(
                market_order_id,
                self._settings.market_campaign_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("get_order_items failed for %s: %s", market_order_id, exc)
            await self._alert(f"Ошибка получения состава заказа #{market_order_id}")
            return

        async with self._db_factory() as db:
            await stock_ops.save_order_items(db, order_uuid, items)

        async with self._db_factory() as db:
            await stock_ops.reserve_materials(db, order_uuid, items)

        await self._update_storefront()

        has_e_items = any("-e" in item.get("sku", "") for item in items)
        if has_e_items:
            async with self._db_factory() as db:
                low = await stock_ops.is_eucalyptus_low(db)
            if low:
                await self._alert_all(
                    "⚠️ Эвкалипт заканчивается. Сколько осталось в холодильнике?",
                    reply_markup=_EVKALIPT_KEYBOARD,
                )

    async def handle_order_ready(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("order.ready missing order_id: %s", data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        async with self._db_factory() as db:
            await stock_ops.debit_materials(db, order_uuid)
            cost = await stock_ops.compute_order_cost(db, order_uuid)
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order:
                order.estimated_cost = cost
                await db.commit()

        await self._update_storefront()

    async def handle_order_released(self, channel: str, data: dict) -> None:
        """Handles both order.cancelled and order.timeout."""
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("%s missing order_id: %s", channel, data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        async with self._db_factory() as db:
            await stock_ops.release_materials(db, order_uuid)

        await self._update_storefront()

    def _parse_command(self, text: str) -> dict | None:
        """Parse a Telegram stock command. Returns parsed dict or None."""
        m = _ARRIVAL_RE.search(text)
        if m:
            return {
                "type": "arrival",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
                "cost_per_unit": _to_decimal(m.group(3)),
            }
        m = _EXTRA_DEBIT_RE.search(text)
        if m:
            return {
                "type": "extra_debit",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
                "order_ref": m.group(3),
            }
        m = _SPOILAGE_RE.search(text)
        if m:
            return {
                "type": "spoilage",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
            }
        return None

    async def handle_telegram_message(self, text: str) -> str | None:
        """Parse and execute a stock command. Returns response text or None if unrecognized."""
        parsed = self._parse_command(text)
        if parsed is None:
            return None

        async with self._db_factory() as db:
            material = await stock_ops.find_material_by_name(db, parsed["material_name"])

        if material is None:
            return f"Сырьё «{parsed['material_name']}» не найдено в базе."

        cmd_type = parsed["type"]

        if cmd_type == "arrival":
            async with self._db_factory() as db:
                mat = await stock_ops.record_arrival(
                    db, material.id, parsed["quantity"], parsed["cost_per_unit"]
                )
            await self._update_storefront()
            return (
                f"✅ Приход: {parsed['quantity']} {mat.unit} «{mat.name}» "
                f"по {parsed['cost_per_unit']}₽. Остаток: {mat.physical_stock} {mat.unit}."
            )

        if cmd_type == "spoilage":
            async with self._db_factory() as db:
                mat = await stock_ops.record_spoilage(db, material.id, parsed["quantity"])
            await self._update_storefront()
            return (
                f"✅ Списано: {parsed['quantity']} {mat.unit} «{mat.name}». "
                f"Остаток: {mat.physical_stock} {mat.unit}."
            )

        if cmd_type == "extra_debit":
            order_ref = parsed["order_ref"]
            async with self._db_factory() as db:
                result = await db.execute(
                    select(Order).where(Order.market_order_id == order_ref)
                )
                order = result.scalar_one_or_none()
                if order is None:
                    return f"Заказ #{order_ref} не найден."
                mat = await stock_ops.record_extra_debit(
                    db,
                    material.id,
                    order.id,
                    parsed["quantity"],
                    note=f"доп. списание к заказу #{order_ref}",
                )
            await self._update_storefront()
            return (
                f"✅ Доп. списание: {parsed['quantity']} {mat.unit} «{mat.name}» "
                f"к заказу #{order_ref}."
            )

        return None
```

- [ ] **Step 2.4: Run all agent tests**

```bash
pytest tests/agents/flower_stock/test_agent.py -v
```

Expected: all tests PASSED (existing + new)

- [ ] **Step 2.5: Commit**

```bash
git add app/agents/flower_stock/agent.py tests/agents/flower_stock/test_agent.py
git commit -m "feat(agent): add eucalyptus alert flow and florist_bot support"
```

---

### Task 3: owner_bot.py — eucalyptus callback registration

**Files:**
- Modify: `app/bot/owner_bot.py`

- [ ] **Step 3.1: Add `register_eucalyptus_callbacks` to owner_bot.py**

Append to `app/bot/owner_bot.py` (before `create_owner_bot`):

```python
def register_eucalyptus_callbacks(flower_stock_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith("evk_restock:")
    )
    async def handle_evk_callback(callback: CallbackQuery):
        await callback.answer()
        qty_g = int(callback.data.split(":", 1)[1])
        await flower_stock_agent.handle_eucalyptus_callback(qty_g)
        label = f"{qty_g}г добавлено" if qty_g else "Не добавлять"
        await callback.message.edit_text(f"✅ {label}")
```

- [ ] **Step 3.2: Verify import works**

```bash
python -c "from app.bot.owner_bot import register_eucalyptus_callbacks; print('ok')"
```

Expected: `ok`

- [ ] **Step 3.3: Commit**

```bash
git add app/bot/owner_bot.py
git commit -m "feat(owner_bot): register eucalyptus restock callbacks"
```

---

### Task 4: florist_bot.py — eucalyptus callback registration

**Files:**
- Modify: `app/bot/florist_bot.py`

- [ ] **Step 4.1: Add `register_eucalyptus_callbacks` to florist_bot.py**

Append to `app/bot/florist_bot.py` (before `create_florist_bot`):

```python
def register_eucalyptus_callbacks(flower_stock_agent) -> None:
    @florist_router.callback_query(
        lambda c: c.data and c.data.startswith("evk_restock:")
    )
    async def handle_evk_callback(callback: CallbackQuery):
        await callback.answer()
        qty_g = int(callback.data.split(":", 1)[1])
        await flower_stock_agent.handle_eucalyptus_callback(qty_g)
        label = f"{qty_g}г добавлено" if qty_g else "Не добавлять"
        await callback.message.edit_text(f"✅ {label}")
```

- [ ] **Step 4.2: Verify import works**

```bash
python -c "from app.bot.florist_bot import register_eucalyptus_callbacks; print('ok')"
```

Expected: `ok`

- [ ] **Step 4.3: Commit**

```bash
git add app/bot/florist_bot.py
git commit -m "feat(florist_bot): register eucalyptus restock callbacks"
```

---

### Task 5: main.py — wire florist_bot into agent and register callbacks

**Files:**
- Modify: `app/main.py`

- [ ] **Step 5.1: Update imports in main.py**

Replace the existing owner_bot import block (lines 11-16):

```python
from app.bot.owner_bot import (
    create_owner_bot,
    register_order_callbacks as register_owner_callbacks,
    register_stock_commands,
    register_pricing_callbacks,
    register_eucalyptus_callbacks as register_owner_eucalyptus_callbacks,
)
```

Replace the existing florist_bot import (line 17):

```python
from app.bot.florist_bot import (
    create_florist_bot,
    register_order_callbacks as register_florist_callbacks,
    register_eucalyptus_callbacks as register_florist_eucalyptus_callbacks,
)
```

- [ ] **Step 5.2: Pass florist_bot to FlowerStockAgent and register callbacks**

Replace line 58 in `app/main.py`:

```python
    flower_stock_agent = FlowerStockAgent(AsyncSessionLocal, owner_bot, settings)
```

With:

```python
    flower_stock_agent = FlowerStockAgent(AsyncSessionLocal, owner_bot, settings, florist_bot=florist_bot)
```

After the existing `register_stock_commands(flower_stock_agent)` line (line 67), add:

```python
    register_owner_eucalyptus_callbacks(flower_stock_agent)
    if florist_bot:
        register_florist_eucalyptus_callbacks(flower_stock_agent)
```

- [ ] **Step 5.3: Verify app imports cleanly**

```bash
python -c "from app.main import app; print('ok')"
```

Expected: `ok`

- [ ] **Step 5.4: Run the full test suite**

```bash
pytest tests/ -v --tb=short
```

Expected: all previously passing tests still pass, new tests pass.

- [ ] **Step 5.5: Commit**

```bash
git add app/main.py
git commit -m "feat(main): wire florist_bot into FlowerStockAgent and register eucalyptus callbacks"
```

---

## Self-Review

**Spec coverage:**
- ✅ stock=0 hides -e SKUs — handled via existing `_update_storefront` after `reserve_materials`
- ✅ Telegram alert with inline keyboard — `_alert_all` + `_EVKALIPT_KEYBOARD`
- ✅ 200/400/600 buttons → update DB + restore storefront — `handle_eucalyptus_callback`
- ✅ "Не добавлять" → keep hidden — `qty_g == 0` early return
- ✅ Both owner and florist receive alert — `_alert_all`
- ✅ florist_bot optional — guarded by `if florist_bot` everywhere
- ✅ StockMovement logged — `set_eucalyptus_stock` adds `type="arrival"` row
- ✅ No new DB migrations needed — uses existing `movement_type` enum

**Type consistency:** `handle_eucalyptus_callback(qty_g: int)` — called from bot callbacks as `int(callback.data.split(":", 1)[1])` ✅; called from tests as integer literal ✅

**No placeholders:** all steps contain complete code ✅
