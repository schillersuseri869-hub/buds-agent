# Order Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Order Agent — уведомления о заказах, таймерная цепочка T+50/T+55/T+57, авто-отправка статуса «Готов» в Market API, кнопки в Telegram для владельца и флориста, восстановление таймеров после рестарта.

**Architecture:** `OrderAgent` класс по образцу `PrintAgent` — подписывается на события шины, ведёт `dict[order_id → list[asyncio.Task]]` для таймеров. Таймеры восстанавливаются из БД при старте. Синхронизация кнопок через Redis `SET NX`. Оба бота (owner + florist) регистрируют один и тот же callback handler через `register_order_callbacks()`.

**Tech Stack:** Python asyncio, aiogram 3.x, httpx, Redis (fakeredis в тестах), SQLAlchemy async, pytest-asyncio STRICT mode.

---

## Файловая карта

| Действие | Файл |
|---|---|
| Create | `app/agents/order_agent/__init__.py` |
| Create | `app/agents/order_agent/market_api.py` |
| Create | `app/agents/order_agent/agent.py` |
| Create | `tests/agents/order_agent/__init__.py` |
| Create | `tests/agents/order_agent/test_market_api.py` |
| Create | `tests/agents/order_agent/test_agent.py` |
| Modify | `app/api/webhooks.py` |
| Modify | `app/bot/owner_bot.py` |
| Modify | `app/bot/florist_bot.py` |
| Modify | `app/main.py` |
| Modify | `tests/test_webhooks.py` |

---

## Task 1: Market API module

**Files:**
- Create: `app/agents/order_agent/__init__.py`
- Create: `app/agents/order_agent/market_api.py`
- Create: `tests/agents/order_agent/__init__.py`
- Create: `tests/agents/order_agent/test_market_api.py`

- [ ] **Step 1: Создать пустые __init__.py**

```bash
touch app/agents/order_agent/__init__.py
touch tests/agents/order_agent/__init__.py
```

- [ ] **Step 2: Написать failing тесты для market_api**

Создать `tests/agents/order_agent/test_market_api.py`:

```python
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.order_agent.market_api import set_order_ready, get_order_status


def _mock_client(method: str, status_code: int = 200, json_body: dict = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_body or {})
    if status_code >= 400:
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("err", request=MagicMock(), response=resp)
        )
    else:
        resp.raise_for_status = MagicMock()
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    setattr(client, method, AsyncMock(return_value=resp))
    return client


@pytest.mark.asyncio
async def test_set_order_ready_sends_put_with_correct_body():
    mock = _mock_client("put")
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        await set_order_ready("YM-123", 148807227, "tok")
    mock.put.assert_awaited_once()
    url, = mock.put.call_args[0]
    assert "/campaigns/148807227/orders/YM-123/status" in url
    assert mock.put.call_args[1]["json"] == {"order": {"status": "READY_TO_SHIP"}}


@pytest.mark.asyncio
async def test_set_order_ready_raises_on_http_error():
    mock = _mock_client("put", status_code=400)
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        with pytest.raises(httpx.HTTPStatusError):
            await set_order_ready("YM-123", 148807227, "tok")


@pytest.mark.asyncio
async def test_get_order_status_returns_status_field():
    mock = _mock_client("get", json_body={"order": {"id": 1, "status": "READY_TO_SHIP"}})
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        result = await get_order_status("YM-123", 148807227, "tok")
    assert result == "READY_TO_SHIP"


@pytest.mark.asyncio
async def test_get_order_status_raises_on_http_error():
    mock = _mock_client("get", status_code=404)
    with patch("app.agents.order_agent.market_api.httpx.AsyncClient", return_value=mock):
        with pytest.raises(httpx.HTTPStatusError):
            await get_order_status("YM-123", 148807227, "tok")
```

- [ ] **Step 3: Запустить тесты — убедиться что падают (ImportError)**

```bash
pytest tests/agents/order_agent/test_market_api.py -v
```
Ожидаем: `ImportError: cannot import name 'set_order_ready'`

- [ ] **Step 4: Реализовать `market_api.py`**

Создать `app/agents/order_agent/market_api.py`:

```python
import httpx

_BASE = "https://api.partner.market.yandex.ru"


async def set_order_ready(market_order_id: str, campaign_id: int, token: str) -> None:
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}/status"
    async with httpx.AsyncClient() as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"order": {"status": "READY_TO_SHIP"}},
            timeout=30.0,
        )
        response.raise_for_status()


async def get_order_status(market_order_id: str, campaign_id: int, token: str) -> str:
    url = f"{_BASE}/campaigns/{campaign_id}/orders/{market_order_id}"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
    return data["order"]["status"]
```

- [ ] **Step 5: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_market_api.py -v
```
Ожидаем: `4 passed`

- [ ] **Step 6: Commit**

```bash
git add app/agents/order_agent/__init__.py app/agents/order_agent/market_api.py tests/agents/order_agent/__init__.py tests/agents/order_agent/test_market_api.py
git commit -m "feat(order-agent): add Market API module (set_order_ready, get_order_status)"
```

---

## Task 2: OrderAgent — скелет, _alert, _notify_all

**Files:**
- Create: `app/agents/order_agent/agent.py`
- Create: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты для _alert и _notify_all**

Создать `tests/agents/order_agent/test_agent.py`:

```python
import json
import uuid
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.order_agent.agent import OrderAgent


def make_agent(redis=None, db_factory=None, owner_bot=None, florist_bot=None, event_bus=None):
    if redis is None:
        redis = fakeredis.aioredis.FakeRedis()
    if db_factory is None:
        db_factory = MagicMock()
    if owner_bot is None:
        owner_bot = AsyncMock()
        owner_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    if event_bus is None:
        event_bus = AsyncMock()

    settings = MagicMock()
    settings.owner_telegram_id = 111111
    settings.market_campaign_id = 148807227
    settings.market_api_token = "test_token"

    return OrderAgent(redis, db_factory, owner_bot, florist_bot, event_bus, settings)


@pytest.mark.asyncio
async def test_alert_sends_message_to_owner():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = make_agent(owner_bot=owner_bot)
    await agent._alert("test alert")
    owner_bot.send_message.assert_awaited_once_with(111111, "test alert")


@pytest.mark.asyncio
async def test_alert_does_not_raise_on_telegram_error():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock(side_effect=Exception("Telegram down"))
    agent = make_agent(owner_bot=owner_bot)
    await agent._alert("test")  # должен не падать


@pytest.mark.asyncio
async def test_notify_all_sends_to_owner_and_florists():
    owner_bot = AsyncMock()
    owner_msg = MagicMock(message_id=42)
    owner_bot.send_message = AsyncMock(return_value=owner_msg)

    florist_bot = AsyncMock()
    florist_msg = MagicMock(message_id=99)
    florist_bot.send_message = AsyncMock(return_value=florist_msg)

    florist = MagicMock()
    florist.telegram_id = 222222

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [florist]
    mock_db.execute = AsyncMock(return_value=mock_result)

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db_factory = MagicMock(return_value=ctx)

    agent = make_agent(db_factory=db_factory, owner_bot=owner_bot, florist_bot=florist_bot)
    result = await agent._notify_all("Hello!")

    owner_bot.send_message.assert_awaited_once()
    florist_bot.send_message.assert_awaited_once()
    assert len(result) == 2
    assert result[0] == (111111, 42, "owner")
    assert result[1] == (222222, 99, "florist")


@pytest.mark.asyncio
async def test_notify_all_skips_florist_if_no_florist_bot():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    agent = make_agent(owner_bot=owner_bot, florist_bot=None)
    result = await agent._notify_all("Hello!")
    assert len(result) == 1
    assert result[0][2] == "owner"
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py::test_alert_sends_message_to_owner -v
```
Ожидаем: `ImportError`

- [ ] **Step 3: Реализовать скелет agent.py**

Создать `app/agents/order_agent/agent.py`:

```python
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.orders import Order
from app.models.florists import Florist
from app.agents.order_agent import market_api

logger = logging.getLogger(__name__)

_REDIS_BTN_TTL = 7200  # 2 hours


async def _sleep_until(target: datetime) -> None:
    delay = (target - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


class OrderAgent:
    def __init__(
        self,
        redis: Redis,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        florist_bot: Bot | None,
        event_bus,
        settings,
    ):
        self._redis = redis
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._florist_bot = florist_bot
        self._event_bus = event_bus
        self._settings = settings
        self._tasks: dict[str, list[asyncio.Task]] = {}

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, message)
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)

    async def _notify_all(self, text: str, keyboard=None) -> list[tuple]:
        messages = []
        try:
            msg = await self._owner_bot.send_message(
                self._settings.owner_telegram_id, text, reply_markup=keyboard
            )
            messages.append((self._settings.owner_telegram_id, msg.message_id, "owner"))
        except Exception as exc:
            logger.error("Failed to notify owner: %s", exc)

        if self._florist_bot:
            async with self._db_factory() as db:
                result = await db.execute(select(Florist).where(Florist.active == True))  # noqa: E712
                florists = list(result.scalars().all())
            for florist in florists:
                try:
                    msg = await self._florist_bot.send_message(
                        florist.telegram_id, text, reply_markup=keyboard
                    )
                    messages.append((florist.telegram_id, msg.message_id, "florist"))
                except Exception as exc:
                    logger.error("Failed to notify florist %s: %s", florist.telegram_id, exc)
        return messages
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "alert or notify" -v
```
Ожидаем: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add OrderAgent skeleton with _alert and _notify_all"
```

---

## Task 3: _run_t50 — таймер с кнопками

**Files:**
- Modify: `app/agents/order_agent/agent.py` (добавить `_run_t50`)
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тест для _run_t50**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_run_t50_sends_buttons_and_saves_to_redis():
    redis = fakeredis.aioredis.FakeRedis()
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock(return_value=MagicMock(message_id=77))
    agent = make_agent(redis=redis, owner_bot=owner_bot)

    order_id = str(uuid.uuid4())
    agent._tasks[order_id] = []  # simulate active order

    fire_at = datetime.now(timezone.utc)  # fire immediately

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t50(order_id, "YM-999", fire_at)

    # Check that message was sent with keyboard
    owner_bot.send_message.assert_awaited_once()
    call_kwargs = owner_bot.send_message.call_args[1]
    assert call_kwargs["reply_markup"] is not None

    # Check Redis storage
    raw = await redis.get(f"order:buttons:{order_id}")
    assert raw is not None
    data = json.loads(raw)
    assert data["market_order_id"] == "YM-999"
    assert len(data["messages"]) == 1
    assert data["messages"][0] == [111111, 77, "owner"]


@pytest.mark.asyncio
async def test_run_t50_skips_if_order_cancelled():
    agent = make_agent()
    order_id = str(uuid.uuid4())
    # _tasks does NOT contain order_id — simulates cancelled order

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t50(order_id, "YM-999", datetime.now(timezone.utc))

    # owner_bot.send_message should NOT be called
    agent._owner_bot.send_message.assert_not_awaited()
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t50" -v
```
Ожидаем: `AttributeError: 'OrderAgent' object has no attribute '_run_t50'`

- [ ] **Step 3: Добавить `_run_t50` в `agent.py`**

Добавить метод в класс `OrderAgent` после `_notify_all`:

```python
    async def _run_t50(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Готов сейчас",
                callback_data=f"ready_now:{order_id}",
            ),
            InlineKeyboardButton(
                text="⏰ Авто через 5 мин",
                callback_data=f"auto_5min:{order_id}",
            ),
        ]])

        messages = await self._notify_all(
            f"⏰ Заказ #{market_order_id} — осталось 7 минут! Выберите действие:",
            keyboard=keyboard,
        )

        redis_data = json.dumps({
            "messages": [list(m) for m in messages],
            "market_order_id": market_order_id,
        })
        await self._redis.setex(f"order:buttons:{order_id}", _REDIS_BTN_TTL, redis_data)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t50" -v
```
Ожидаем: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add _run_t50 with inline keyboard and Redis button storage"
```

---

## Task 4: _run_t55 — авто-отправка «Готов» + polling подтверждения

**Files:**
- Modify: `app/agents/order_agent/agent.py`
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты для _run_t55**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_run_t55_publishes_order_ready_on_confirmation():
    event_bus = AsyncMock()
    order_id = str(uuid.uuid4())
    agent = make_agent(event_bus=event_bus)
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "waiting"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.asyncio.sleep", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.set_order_ready", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.get_order_status",
               new_callable=AsyncMock, return_value="READY_TO_SHIP"):
        await agent._run_t55(order_id, "YM-555", datetime.now(timezone.utc))

    event_bus.publish.assert_awaited_once()
    call_args = event_bus.publish.call_args
    assert call_args[0][0] == "order.ready"
    assert call_args[0][1]["order_id"] == order_id


@pytest.mark.asyncio
async def test_run_t55_alerts_if_confirmation_fails():
    order_id = str(uuid.uuid4())
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = make_agent(owner_bot=owner_bot)
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "waiting"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.asyncio.sleep", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.set_order_ready", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.get_order_status",
               new_callable=AsyncMock, return_value="PROCESSING"):
        await agent._run_t55(order_id, "YM-555", datetime.now(timezone.utc))

    owner_bot.send_message.assert_awaited_once()
    alert_text = owner_bot.send_message.call_args[0][1]
    assert "не подтверждён" in alert_text


@pytest.mark.asyncio
async def test_run_t55_alerts_if_set_order_ready_fails_all_retries():
    order_id = str(uuid.uuid4())
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = make_agent(owner_bot=owner_bot)
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "waiting"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.asyncio.sleep", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.set_order_ready",
               new_callable=AsyncMock, side_effect=Exception("API down")):
        await agent._run_t55(order_id, "YM-555", datetime.now(timezone.utc))

    owner_bot.send_message.assert_awaited_once()
    alert_text = owner_bot.send_message.call_args[0][1]
    assert "Зайдите в Маркет вручную" in alert_text


@pytest.mark.asyncio
async def test_run_t55_skips_if_order_not_waiting():
    order_id = str(uuid.uuid4())
    agent = make_agent()
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "ready"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock), \
         patch("app.agents.order_agent.agent.market_api.set_order_ready",
               new_callable=AsyncMock) as mock_set:
        await agent._run_t55(order_id, "YM-555", datetime.now(timezone.utc))

    mock_set.assert_not_awaited()
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t55" -v
```
Ожидаем: `AttributeError: '_run_t55'`

- [ ] **Step 3: Добавить `_run_t55` в `agent.py`**

Добавить метод после `_run_t50`:

```python
    async def _run_t55(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
            order = result.scalar_one_or_none()
        if order is None or order.status != "waiting":
            return

        success = False
        for attempt in range(3):
            try:
                await market_api.set_order_ready(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                success = True
                break
            except Exception as exc:
                logger.error("set_order_ready attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

        if not success:
            await self._alert(
                f"⚠️ Не удалось установить статус 'Готов' для заказа #{market_order_id}."
                f" Зайдите в Маркет вручную."
            )
            return

        confirmed = False
        for _ in range(3):
            await asyncio.sleep(40)
            if order_id not in self._tasks:
                return
            try:
                status = await market_api.get_order_status(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                if status == "READY_TO_SHIP":
                    confirmed = True
                    break
            except Exception as exc:
                logger.error("get_order_status failed: %s", exc)

        if confirmed:
            await self._event_bus.publish("order.ready", {
                "order_id": order_id,
                "market_order_id": market_order_id,
                "source": "auto_t55",
            })
        else:
            await self._alert(
                f"🚨 Статус заказа #{market_order_id} не подтверждён Маркетом!"
                f" ~180 сек — зайдите в Маркет вручную."
            )
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t55" -v
```
Ожидаем: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add _run_t55 with Market API retry and polling confirmation"
```

---

## Task 5: _run_t57 + _schedule_timers + handle_order_created

**Files:**
- Modify: `app/agents/order_agent/agent.py`
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_run_t57_sets_timed_out_and_publishes_event():
    event_bus = AsyncMock()
    order_id = str(uuid.uuid4())
    agent = make_agent(event_bus=event_bus)
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "waiting"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t57(order_id, "YM-777", datetime.now(timezone.utc))

    assert mock_order.status == "timed_out"
    mock_db.commit.assert_awaited()
    event_bus.publish.assert_awaited_once()
    assert event_bus.publish.call_args[0][0] == "order.timeout"
    assert order_id not in agent._tasks


@pytest.mark.asyncio
async def test_run_t57_skips_if_order_already_ready():
    event_bus = AsyncMock()
    order_id = str(uuid.uuid4())
    agent = make_agent(event_bus=event_bus)
    agent._tasks[order_id] = []

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "ready"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t57(order_id, "YM-777", datetime.now(timezone.utc))

    event_bus.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_order_created_sets_deadline_and_schedules_timers():
    order_id = str(uuid.uuid4())
    agent = make_agent()

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.timer_deadline = None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch.object(agent, "_schedule_timers") as mock_schedule, \
         patch.object(agent, "_notify_all", new_callable=AsyncMock, return_value=[]):
        await agent.handle_order_created("order.created", {
            "order_id": order_id,
            "market_order_id": "YM-111",
        })

    assert mock_order.timer_deadline is not None
    mock_schedule.assert_called_once_with(order_id, "YM-111", mock_order.timer_deadline)


@pytest.mark.asyncio
async def test_handle_order_created_ignores_missing_fields():
    agent = make_agent()
    await agent.handle_order_created("order.created", {})
    # Should not raise


@pytest.mark.asyncio
async def test_schedule_timers_creates_tasks_for_future_checkpoints():
    agent = make_agent()
    order_id = str(uuid.uuid4())
    deadline = datetime.now(timezone.utc) + timedelta(minutes=57)

    with patch("app.agents.order_agent.agent.asyncio.create_task", return_value=MagicMock()) as mock_create:
        agent._schedule_timers(order_id, "YM-123", deadline)

    assert mock_create.call_count == 3
    assert order_id in agent._tasks
    assert len(agent._tasks[order_id]) == 3
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t57 or handle_order_created or schedule_timers" -v
```
Ожидаем: `AttributeError`

- [ ] **Step 3: Добавить `_run_t57`, `_schedule_timers`, `handle_order_created` в `agent.py`**

Добавить методы в класс `OrderAgent`:

```python
    async def _run_t57(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
            order = result.scalar_one_or_none()
            if order is None or order.status != "waiting":
                return
            order.status = "timed_out"
            await db.commit()

        self._tasks.pop(order_id, None)
        await self._event_bus.publish("order.timeout", {
            "order_id": order_id,
            "market_order_id": market_order_id,
        })
        await self._alert(f"⚠️ Просрочка заказа #{market_order_id}! Зайдите в Маркет вручную.")

    def _schedule_timers(self, order_id: str, market_order_id: str, deadline: datetime) -> None:
        now = datetime.now(timezone.utc)
        t50 = deadline - timedelta(minutes=7)
        t55 = deadline - timedelta(minutes=2)
        t57 = deadline

        tasks = []
        if t50 > now:
            tasks.append(asyncio.create_task(self._run_t50(order_id, market_order_id, t50)))
        if t55 > now:
            tasks.append(asyncio.create_task(self._run_t55(order_id, market_order_id, t55)))
        if t57 > now:
            tasks.append(asyncio.create_task(self._run_t57(order_id, market_order_id, t57)))

        if tasks:
            self._tasks[order_id] = tasks

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

        deadline = datetime.now(timezone.utc) + timedelta(minutes=57)

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order is None:
                logger.error("Order not found in DB: %s", order_id_str)
                return
            order.timer_deadline = deadline
            await db.commit()

        await self._notify_all(f"🌸 Новый заказ #{market_order_id}!")
        self._schedule_timers(order_id_str, market_order_id, deadline)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "t57 or handle_order_created or schedule_timers" -v
```
Ожидаем: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add _run_t57, _schedule_timers, handle_order_created"
```

---

## Task 6: handle_order_status + cancel_timers + _clear_button_messages

**Files:**
- Modify: `app/agents/order_agent/agent.py`
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_cancel_timers_cancels_all_tasks():
    agent = make_agent()
    order_id = str(uuid.uuid4())
    task1 = MagicMock()
    task2 = MagicMock()
    agent._tasks[order_id] = [task1, task2]

    agent.cancel_timers(order_id)

    task1.cancel.assert_called_once()
    task2.cancel.assert_called_once()
    assert order_id not in agent._tasks


@pytest.mark.asyncio
async def test_cancel_timers_is_idempotent():
    agent = make_agent()
    agent.cancel_timers("nonexistent-id")  # должен не падать


@pytest.mark.asyncio
async def test_handle_order_status_ready_updates_db_and_cancels_timers():
    order_id = str(uuid.uuid4())
    agent = make_agent()
    task = MagicMock()
    agent._tasks[order_id] = [task]

    mock_db = AsyncMock()
    mock_order = MagicMock()
    mock_order.status = "waiting"
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=mock_order)
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch.object(agent, "_clear_button_messages", new_callable=AsyncMock):
        await agent.handle_order_status("order.ready", {
            "order_id": order_id,
            "market_order_id": "YM-100",
        })

    assert mock_order.status == "ready"
    task.cancel.assert_called_once()
    assert order_id not in agent._tasks


@pytest.mark.asyncio
async def test_clear_button_messages_edits_all_messages():
    redis = fakeredis.aioredis.FakeRedis()
    order_id = str(uuid.uuid4())
    owner_bot = AsyncMock()
    owner_bot.edit_message_reply_markup = AsyncMock()
    florist_bot = AsyncMock()
    florist_bot.edit_message_reply_markup = AsyncMock()
    agent = make_agent(redis=redis, owner_bot=owner_bot, florist_bot=florist_bot)

    btn_data = json.dumps({
        "messages": [[111111, 42, "owner"], [222222, 99, "florist"]],
        "market_order_id": "YM-100",
    })
    await redis.setex(f"order:buttons:{order_id}", 7200, btn_data)

    await agent._clear_button_messages(order_id)

    owner_bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=111111, message_id=42, reply_markup=None
    )
    florist_bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=222222, message_id=99, reply_markup=None
    )
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "cancel_timers or handle_order_status or clear_button" -v
```
Ожидаем: `AttributeError`

- [ ] **Step 3: Добавить методы в `agent.py`**

```python
    def cancel_timers(self, order_id: str) -> None:
        tasks = self._tasks.pop(order_id, [])
        for task in tasks:
            task.cancel()

    async def _clear_button_messages(self, order_id: str) -> None:
        raw = await self._redis.get(f"order:buttons:{order_id}")
        if not raw:
            return
        button_data = json.loads(raw)
        for entry in button_data.get("messages", []):
            chat_id, message_id, bot_type = entry
            bot = self._owner_bot if bot_type == "owner" else self._florist_bot
            if bot is None:
                continue
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=None
                )
            except Exception as exc:
                logger.error("Failed to clear buttons chat=%s msg=%s: %s", chat_id, message_id, exc)

    async def handle_order_status(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("order status event missing order_id: %s", data)
            return

        status_map = {
            "order.ready": "ready",
            "order.cancelled": "cancelled",
            "order.shipped": "shipped",
            "order.delivered": "delivered",
        }
        new_status = status_map.get(channel)
        if new_status is None:
            return

        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order and order.status == "waiting":
                order.status = new_status
                await db.commit()

        self.cancel_timers(order_id_str)
        await self._clear_button_messages(order_id_str)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "cancel_timers or handle_order_status or clear_button" -v
```
Ожидаем: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add cancel_timers, handle_order_status, _clear_button_messages"
```

---

## Task 7: recover_timers

**Files:**
- Modify: `app/agents/order_agent/agent.py`
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_recover_timers_schedules_future_orders():
    order_id = str(uuid.uuid4())
    agent = make_agent()

    future_deadline = datetime.now(timezone.utc) + timedelta(minutes=30)
    mock_order = MagicMock()
    mock_order.id = uuid.UUID(order_id)
    mock_order.market_order_id = "YM-RECOVER"
    mock_order.status = "waiting"
    mock_order.timer_deadline = future_deadline

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_order]
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch.object(agent, "_schedule_timers") as mock_schedule:
        await agent.recover_timers()

    mock_schedule.assert_called_once_with(order_id, "YM-RECOVER", future_deadline)


@pytest.mark.asyncio
async def test_recover_timers_immediately_times_out_past_deadline():
    order_id = str(uuid.uuid4())
    event_bus = AsyncMock()
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = make_agent(event_bus=event_bus, owner_bot=owner_bot)

    past_deadline = datetime.now(timezone.utc) - timedelta(minutes=5)
    mock_order = MagicMock()
    mock_order.id = uuid.UUID(order_id)
    mock_order.market_order_id = "YM-PAST"
    mock_order.status = "waiting"
    mock_order.timer_deadline = past_deadline

    # First db call returns waiting orders list
    # Second db call (inside past deadline handling) returns fresh order
    fresh_order = MagicMock()
    fresh_order.status = "waiting"

    call_count = 0

    async def side_effect_execute(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_result = MagicMock()
        if call_count == 1:
            mock_result.scalars.return_value.all.return_value = [mock_order]
        else:
            mock_result.scalar_one_or_none = MagicMock(return_value=fresh_order)
        return mock_result

    mock_db = AsyncMock()
    mock_db.execute = side_effect_execute
    mock_db.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch.object(agent, "_schedule_timers") as mock_schedule:
        await agent.recover_timers()

    mock_schedule.assert_not_called()
    event_bus.publish.assert_awaited_once()
    assert event_bus.publish.call_args[0][0] == "order.timeout"
    owner_bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_recover_timers_skips_orders_without_deadline():
    agent = make_agent()

    mock_order = MagicMock()
    mock_order.timer_deadline = None
    mock_order.status = "waiting"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [mock_order]
    mock_db.execute = AsyncMock(return_value=mock_result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    agent._db_factory = MagicMock(return_value=ctx)

    with patch.object(agent, "_schedule_timers") as mock_schedule:
        await agent.recover_timers()

    mock_schedule.assert_not_called()
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "recover" -v
```
Ожидаем: `AttributeError: 'OrderAgent' object has no attribute 'recover_timers'`

- [ ] **Step 3: Добавить `recover_timers` в `agent.py`**

```python
    async def recover_timers(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.status == "waiting"))
            orders = list(result.scalars().all())

        for order in orders:
            if order.timer_deadline is None:
                continue
            order_id = str(order.id)
            if order.timer_deadline <= now:
                async with self._db_factory() as db:
                    result = await db.execute(select(Order).where(Order.id == order.id))
                    fresh = result.scalar_one_or_none()
                    if fresh and fresh.status == "waiting":
                        fresh.status = "timed_out"
                        await db.commit()
                await self._event_bus.publish("order.timeout", {
                    "order_id": order_id,
                    "market_order_id": order.market_order_id,
                })
                await self._alert(
                    f"⚠️ Просрочка при восстановлении: заказ #{order.market_order_id}"
                )
            else:
                self._schedule_timers(order_id, order.market_order_id, order.timer_deadline)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "recover" -v
```
Ожидаем: `3 passed`

- [ ] **Step 5: Запустить все тесты агента**

```bash
pytest tests/agents/order_agent/test_agent.py -v
```
Ожидаем: все тесты проходят.

- [ ] **Step 6: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add recover_timers with past-deadline handling"
```

---

## Task 8: handle_button_callback — атомарное нажатие кнопки

**Files:**
- Modify: `app/agents/order_agent/agent.py`
- Modify: `tests/agents/order_agent/test_agent.py`

- [ ] **Step 1: Написать failing тесты**

Добавить в конец `tests/agents/order_agent/test_agent.py`:

```python
@pytest.mark.asyncio
async def test_handle_button_callback_ready_now_cancels_timers_and_calls_api():
    redis = fakeredis.aioredis.FakeRedis()
    order_id = str(uuid.uuid4())
    event_bus = AsyncMock()
    owner_bot = AsyncMock()
    owner_bot.edit_message_text = AsyncMock()
    agent = make_agent(redis=redis, owner_bot=owner_bot, event_bus=event_bus)
    task = MagicMock()
    agent._tasks[order_id] = [task]

    btn_data = json.dumps({
        "messages": [[111111, 42, "owner"]],
        "market_order_id": "YM-BTN",
    })
    await redis.setex(f"order:buttons:{order_id}", 7200, btn_data)

    callback = AsyncMock()
    callback.data = f"ready_now:{order_id}"
    callback.answer = AsyncMock()

    with patch("app.agents.order_agent.agent.market_api.set_order_ready", new_callable=AsyncMock):
        await agent.handle_button_callback(callback)

    callback.answer.assert_awaited()
    task.cancel.assert_called_once()
    event_bus.publish.assert_awaited_once()
    assert event_bus.publish.call_args[0][0] == "order.ready"
    owner_bot.edit_message_text.assert_awaited()


@pytest.mark.asyncio
async def test_handle_button_callback_auto_5min_does_not_cancel_timers():
    redis = fakeredis.aioredis.FakeRedis()
    order_id = str(uuid.uuid4())
    owner_bot = AsyncMock()
    owner_bot.edit_message_text = AsyncMock()
    agent = make_agent(redis=redis, owner_bot=owner_bot)
    task = MagicMock()
    agent._tasks[order_id] = [task]

    btn_data = json.dumps({
        "messages": [[111111, 42, "owner"]],
        "market_order_id": "YM-BTN",
    })
    await redis.setex(f"order:buttons:{order_id}", 7200, btn_data)

    callback = AsyncMock()
    callback.data = f"auto_5min:{order_id}"
    callback.answer = AsyncMock()

    await agent.handle_button_callback(callback)

    task.cancel.assert_not_called()
    owner_bot.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_button_callback_second_press_is_rejected():
    redis = fakeredis.aioredis.FakeRedis()
    order_id = str(uuid.uuid4())
    owner_bot = AsyncMock()
    owner_bot.edit_message_text = AsyncMock()
    agent = make_agent(redis=redis, owner_bot=owner_bot)

    btn_data = json.dumps({"messages": [], "market_order_id": "YM-BTN"})
    await redis.setex(f"order:buttons:{order_id}", 7200, btn_data)

    # Simulate first press already recorded
    await redis.set(f"order:buttons:pressed:{order_id}", "1", ex=7200)

    callback = AsyncMock()
    callback.data = f"ready_now:{order_id}"
    callback.answer = AsyncMock()

    with patch("app.agents.order_agent.agent.market_api.set_order_ready",
               new_callable=AsyncMock) as mock_api:
        await agent.handle_button_callback(callback)

    mock_api.assert_not_awaited()
    # Answer should say "already taken"
    call_text = callback.answer.call_args[0][0]
    assert "Уже" in call_text
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/agents/order_agent/test_agent.py -k "button_callback" -v
```
Ожидаем: `AttributeError: 'OrderAgent' object has no attribute 'handle_button_callback'`

- [ ] **Step 3: Добавить `handle_button_callback` в `agent.py`**

```python
    async def handle_button_callback(self, callback) -> None:
        data = callback.data
        action, order_id = data.split(":", 1)

        pressed_key = f"order:buttons:pressed:{order_id}"
        was_first = await self._redis.set(pressed_key, "1", nx=True, ex=_REDIS_BTN_TTL)
        if not was_first:
            await callback.answer("Уже принято другим пользователем")
            return

        await callback.answer("Принято!")

        raw = await self._redis.get(f"order:buttons:{order_id}")
        market_order_id = ""
        messages = []
        if raw:
            btn_data = json.loads(raw)
            market_order_id = btn_data.get("market_order_id", "")
            messages = btn_data.get("messages", [])

        if action == "ready_now":
            self.cancel_timers(order_id)
            action_text = "Готов сейчас"
            try:
                await market_api.set_order_ready(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                await self._event_bus.publish("order.ready", {
                    "order_id": order_id,
                    "market_order_id": market_order_id,
                    "source": "button",
                })
            except Exception as exc:
                logger.error("set_order_ready failed from button: %s", exc)
                await self._alert(
                    f"⚠️ Не удалось установить статус 'Готов' для заказа #{market_order_id}."
                    f" Зайдите в Маркет вручную."
                )
        else:
            action_text = "Авто через 5 мин"

        for entry in messages:
            chat_id, message_id, bot_type = entry
            bot = self._owner_bot if bot_type == "owner" else self._florist_bot
            if bot is None:
                continue
            try:
                await bot.edit_message_text(
                    text=f"✅ {action_text} — принято",
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except Exception as exc:
                logger.error("Failed to edit button message: %s", exc)
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
pytest tests/agents/order_agent/test_agent.py -k "button_callback" -v
```
Ожидаем: `3 passed`

- [ ] **Step 5: Запустить полный набор тестов агента**

```bash
pytest tests/agents/order_agent/ -v
```
Ожидаем: все тесты проходят.

- [ ] **Step 6: Commit**

```bash
git add app/agents/order_agent/agent.py tests/agents/order_agent/test_agent.py
git commit -m "feat(order-agent): add handle_button_callback with Redis atomic press detection"
```

---

## Task 9: Расширение webhooks.py — статус-маппинг + events_log

**Files:**
- Modify: `app/api/webhooks.py`
- Modify: `tests/test_webhooks.py`

Маппинг Яндекс Маркет статусов:

| Market status | Внутреннее событие | DB статус |
|---|---|---|
| `PROCESSING` | `order.created` | `waiting` |
| `READY_TO_SHIP` | `order.ready` | `ready` |
| `SHIPPED` | `order.shipped` | `shipped` |
| `DELIVERED` | `order.delivered` | `delivered` |
| `CANCELLED` | `order.cancelled` | `cancelled` |
| `CANCELLED_IN_DELIVERY` | `order.cancelled` | `cancelled` |

- [ ] **Step 1: Написать failing тесты**

Добавить в `tests/test_webhooks.py`:

```python
@pytest.mark.asyncio
async def test_webhook_cancelled_status_publishes_order_cancelled():
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.main import app

    mock_bus = AsyncMock()
    mock_bus.publish = AsyncMock()

    payload = {
        "type": "ORDER_STATUS_CHANGED",
        "orderId": "YM-CANCEL-001",
        "status": "CANCELLED",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # First create the order
        await client.post("/webhooks/market", json={
            "type": "ORDER_STATUS_CHANGED",
            "orderId": "YM-CANCEL-001",
            "status": "PROCESSING",
        })
        # Inject mock bus
        app.state.event_bus = mock_bus
        response = await client.post("/webhooks/market", json=payload)

    assert response.status_code == 200
    # Check that order.cancelled was published
    published_channels = [c[0][0] for c in mock_bus.publish.await_args_list]
    assert "order.cancelled" in published_channels


@pytest.mark.asyncio
async def test_webhook_logs_all_payloads_to_events_log():
    from app.main import app
    from app.database import AsyncSessionLocal
    from sqlalchemy import select
    from app.models.events_log import EventLog

    payload = {
        "type": "ORDER_STATUS_CHANGED",
        "orderId": "YM-LOG-001",
        "status": "PROCESSING",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/webhooks/market", json=payload)

    assert response.status_code == 200
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(EventLog).where(EventLog.event_type == "market_webhook")
        )
        log = result.scalars().first()
    assert log is not None
    assert log.payload["orderId"] == "YM-LOG-001"
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
pytest tests/test_webhooks.py -v
```

- [ ] **Step 3: Переписать `app/api/webhooks.py`**

```python
from fastapi import APIRouter, Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models.orders import Order
from app.models.events_log import EventLog

router = APIRouter()

_MARKET_STATUS_MAP = {
    "PROCESSING": ("order.created", "waiting"),
    "READY_TO_SHIP": ("order.ready", "ready"),
    "SHIPPED": ("order.shipped", "shipped"),
    "DELIVERED": ("order.delivered", "delivered"),
    "CANCELLED": ("order.cancelled", "cancelled"),
    "CANCELLED_IN_DELIVERY": ("order.cancelled", "cancelled"),
}


@router.post("/market")
async def market_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    payload = await request.json()
    market_order_id = str(payload.get("orderId", "")).strip()
    market_status = str(payload.get("status", "PROCESSING")).strip()

    if not market_order_id:
        return {"status": "ignored", "reason": "no orderId"}

    # Log every incoming webhook
    log_entry = EventLog(event_type="market_webhook", payload=payload)
    db.add(log_entry)
    await db.flush()

    bus = getattr(request.app.state, "event_bus", None)
    event_name, db_status = _MARKET_STATUS_MAP.get(market_status, ("order.created", "waiting"))

    result = await db.execute(
        select(Order).where(Order.market_order_id == market_order_id)
    )
    order = result.scalar_one_or_none()

    if order is None:
        # New order — only on PROCESSING
        if event_name != "order.created":
            await db.commit()
            return {"status": "ignored", "reason": "order not found"}
        try:
            order = Order(market_order_id=market_order_id, status="waiting")
            db.add(order)
            await db.commit()
            await db.refresh(order)
        except IntegrityError:
            await db.rollback()
            result = await db.execute(
                select(Order).where(Order.market_order_id == market_order_id)
            )
            order = result.scalar_one()
            return {"status": "ok", "order_id": str(order.id)}

        if bus is not None:
            await bus.publish("order.created", {
                "order_id": str(order.id),
                "market_order_id": market_order_id,
            })
    else:
        # Existing order — update status if changed
        if event_name != "order.created" and order.status != db_status:
            order.status = db_status
            await db.commit()
            if bus is not None:
                await bus.publish(event_name, {
                    "order_id": str(order.id),
                    "market_order_id": market_order_id,
                })
        else:
            await db.commit()

    return {"status": "ok", "order_id": str(order.id)}
```

- [ ] **Step 4: Запустить тесты вебхука**

```bash
pytest tests/test_webhooks.py -v
```
Ожидаем: все тесты проходят.

- [ ] **Step 5: Commit**

```bash
git add app/api/webhooks.py tests/test_webhooks.py
git commit -m "feat(webhooks): add Market status routing, events_log logging"
```

---

## Task 10: Bot callback registration

**Files:**
- Modify: `app/bot/owner_bot.py`
- Modify: `app/bot/florist_bot.py`

- [ ] **Step 1: Добавить `register_order_callbacks` в `owner_bot.py`**

Открыть `app/bot/owner_bot.py`. Заменить полностью:

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from app.config import settings

owner_router = Router()


@owner_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("BUDS администратор готов к работе.")


@owner_router.message(Command("status"))
async def cmd_status(message: Message):
    await message.answer("Статус: онлайн.")


def register_order_callbacks(order_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith(("ready_now:", "auto_5min:"))
    )
    async def handle_order_callback(callback: CallbackQuery):
        await order_agent.handle_button_callback(callback)


def create_owner_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher()
    dp.include_router(owner_router)
    return bot, dp
```

- [ ] **Step 2: Добавить `register_order_callbacks` в `florist_bot.py`**

Открыть `app/bot/florist_bot.py`. Заменить полностью:

```python
from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from app.config import settings

florist_router = Router()


@florist_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Флорист подключён. Ожидаю заказы.")


def register_order_callbacks(order_agent) -> None:
    @florist_router.callback_query(
        lambda c: c.data and c.data.startswith(("ready_now:", "auto_5min:"))
    )
    async def handle_order_callback(callback: CallbackQuery):
        await order_agent.handle_button_callback(callback)


def create_florist_bot() -> tuple[Bot, Dispatcher] | None:
    if not settings.florist_bot_token:
        return None
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher()
    dp.include_router(florist_router)
    return bot, dp
```

- [ ] **Step 3: Запустить существующие тесты — убедиться ничего не сломалось**

```bash
pytest tests/ -v --ignore=tests/agents/order_agent
```
Ожидаем: все тесты проходят.

- [ ] **Step 4: Commit**

```bash
git add app/bot/owner_bot.py app/bot/florist_bot.py
git commit -m "feat(bots): add register_order_callbacks for inline button handling"
```

---

## Task 11: main.py — финальное подключение

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Обновить `app/main.py`**

Заменить полностью:

```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api.webhooks import router as webhooks_router
from app.api.ws_print import router as ws_router, set_callbacks
from app.bot.owner_bot import create_owner_bot, register_order_callbacks as register_owner_callbacks
from app.bot.florist_bot import create_florist_bot, register_order_callbacks as register_florist_callbacks
from app.core.event_bus import EventBus
from app.database import AsyncSessionLocal
from app.config import settings
from app.agents.print_agent.agent import PrintAgent
from app.agents.order_agent.agent import OrderAgent


@asynccontextmanager
async def lifespan(app: FastAPI):
    owner_bot, owner_dp = create_owner_bot()
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

    print_agent = PrintAgent(redis, AsyncSessionLocal, owner_bot, settings)
    await event_bus.subscribe("order.created", print_agent.handle_order_created)
    set_callbacks(
        on_connect=print_agent.flush_pending_jobs,
        on_ack=print_agent.handle_ack,
    )

    order_agent = OrderAgent(redis, AsyncSessionLocal, owner_bot, florist_bot, event_bus, settings)
    await event_bus.subscribe("order.created", order_agent.handle_order_created)
    await event_bus.subscribe("order.ready", order_agent.handle_order_status)
    await event_bus.subscribe("order.cancelled", order_agent.handle_order_status)
    await event_bus.subscribe("order.shipped", order_agent.handle_order_status)
    await event_bus.subscribe("order.delivered", order_agent.handle_order_status)
    await order_agent.recover_timers()

    register_owner_callbacks(order_agent)
    if florist_bot:
        register_florist_callbacks(order_agent)

    yield

    owner_task.cancel()
    await owner_bot.session.close()
    if florist_task:
        florist_task.cancel()
    if florist_bot:
        await florist_bot.session.close()
    await event_bus.close()
    await redis.aclose()


app = FastAPI(title="BUDS Agent", version="1.0.0", lifespan=lifespan)

app.include_router(webhooks_router, prefix="/webhooks")
app.include_router(ws_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 2: Запустить полный набор тестов**

```bash
pytest tests/ -v
```
Ожидаем: все тесты проходят.

- [ ] **Step 3: Commit**

```bash
git add app/main.py
git commit -m "feat(main): wire up OrderAgent — subscriptions, recover_timers, bot callbacks"
```

---

## Итоговая проверка

- [ ] **Запустить все тесты**

```bash
pytest tests/ -v
```
Ожидаем: все тесты проходят, 0 ошибок.

- [ ] **Проверить импорты (нет сломанных зависимостей)**

```bash
python -c "from app.agents.order_agent.agent import OrderAgent; print('OK')"
python -c "from app.api.webhooks import router; print('OK')"
```

- [ ] **Финальный commit**

```bash
git add -A
git status  # убедиться что нечего лишнего
git commit -m "feat(order-agent): complete Order Agent implementation" --allow-empty
```
