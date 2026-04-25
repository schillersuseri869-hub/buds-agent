# Order Agent — Design Spec

**Date:** 2026-04-26
**Project:** BUDS Agent (Яндекс Маркет FBS)

---

## Цель

Order Agent управляет жизненным циклом заказа: уведомляет владельца и флориста, запускает таймерную цепочку сборки, отправляет статус «Готов» в Маркет API и фиксирует просрочки.

---

## Контекст

- У продавца есть **60 минут** от поступления заказа для установки статуса «Готов к отгрузке» в Яндекс Маркете. За просрочку — штраф.
- Принимаем **3 минуты лага** (задержка вебхука, сеть, Telegram). Внутренний дедлайн: `timer_deadline = created_at + 57 мин`.
- Все datetime хранятся в **UTC**. Времена из Market API (Москва, UTC+3) явно конвертируются при парсинге.

---

## Таймерная цепочка

| Checkpoint | Время от создания | Действие |
|---|---|---|
| **T+50** | 50 мин | Сообщение + inline-кнопки «Готов сейчас» / «Авто через 5 мин» → владелец + все активные флористы |
| **T+55** | 55 мин | Авто-отправка статуса «Готов» в Market API → polling GET подтверждения (до 120 сек) |
| **T+57** | 57 мин | `order.timeout` → алерт владельцу «Просрочка заказа #X», фиксация в БД |

T+45 отсутствует намеренно.

---

## Компоненты

### `app/agents/order_agent/agent.py` — класс `OrderAgent`

**Конструктор:** принимает `redis`, `db_factory`, `owner_bot`, `florist_bot`, `settings`.

**Словарь задач:** `_tasks: dict[str, list[asyncio.Task]]` — `order_id → [task_t50, task_t55, task_t57]`.

**Публичные методы:**

- `handle_order_created(channel, data)` — подписан на `order.created`:
  1. Записывает `timer_deadline = created_at + 57 мин` в БД (обновляет запись Order)
  2. Шлёт уведомление о новом заказе владельцу и всем активным флористам (текст, без кнопок)
  3. Создаёт 3 asyncio задачи: `_run_t50`, `_run_t55`, `_run_t57`

- `handle_order_status(channel, data)` — подписан на `order.ready`, `order.cancelled`:
  1. Обновляет статус Order в БД
  2. Вызывает `cancel_timers(order_id)`
  3. Убирает кнопки из всех сообщений T+50 (редактирует через Telegram API)

- `recover_timers()` — вызывается при старте приложения:
  1. Читает из БД все Orders со статусом `waiting`
  2. Если `timer_deadline <= now()` — сразу публикует `order.timeout`, обновляет статус `timed_out`, пропускает
  3. Для остальных вычисляет какие из T50/T55/T57 ещё в будущем
  4. Создаёт только будущие задачи

- `cancel_timers(order_id)` — отменяет все активные задачи заказа, удаляет из `_tasks`

**Приватные методы:**

- `_run_t50(order_id, fire_at)`:
  1. `asyncio.sleep` до `fire_at`
  2. Шлёт сообщение с inline-кнопками: `ready_now:{order_id}` / `auto_5min:{order_id}`
  3. Сохраняет в Redis `order:buttons:{order_id}` = `{messages: [(chat_id, message_id, bot_type), ...], pressed: false}`, TTL = 2 часа

- `_run_t55(order_id, fire_at)`:
  1. `asyncio.sleep` до `fire_at`
  2. Проверяет статус Order в БД — если уже `ready`/`cancelled`, выходит
  3. Вызывает `market_api.set_order_ready()` с retry (3 попытки, экспоненциальный backoff)
  4. Polling `market_api.get_order_status()` каждые 40 сек, до 3 раз (итого 120 сек)
  5. Статус подтверждён → публикует `order.ready` на шину → `cancel_timers` отменит T57
  6. Не подтверждён → алерт владельцу «Статус не подтверждён, зайдите в Маркет вручную»

- `_run_t57(order_id, fire_at)`:
  1. `asyncio.sleep` до `fire_at`
  2. Проверяет статус Order в БД — если уже `ready`/`cancelled`, выходит (гонка с T+55)
  3. Обновляет Order.status = `timed_out` в БД
  4. Публикует `order.timeout` на шину
  5. Шлёт алерт владельцу: «⚠️ Просрочка заказа #{market_order_id}! Зайдите в Маркет вручную»

- `_notify_all(text, keyboard=None) → list[tuple]`:
  1. Шлёт сообщение в owner_bot
  2. Читает всех активных флористов из БД, шлёт в florist_bot
  3. Возвращает список `[(chat_id, message_id, bot_type)]` для последующего редактирования

- `_alert(message)` — шлёт только владельцу (аналог PrintAgent)

---

### `app/agents/order_agent/market_api.py`

- `set_order_ready(market_order_id, campaign_id, token) → bool`:
  PUT `/campaigns/{campaign_id}/orders/{market_order_id}/status`
  body: `{"order": {"status": "READY_TO_SHIP"}}`
  Возвращает `True` при HTTP 200, иначе бросает исключение.

- `get_order_status(market_order_id, campaign_id, token) → str`:
  GET `/campaigns/{campaign_id}/orders/{market_order_id}`
  Возвращает значение `order.status` из ответа.

---

### `app/api/webhooks.py` — расширение существующего обработчика

Маппинг статусов Яндекс Маркета на внутренние события шины:

| Market status | Внутреннее событие | Новый статус в БД |
|---|---|---|
| `PROCESSING` | `order.created` | `waiting` |
| `READY_TO_SHIP` | `order.ready` | `ready` |
| `SHIPPED` | `order.shipped` | `shipped` |
| `DELIVERED` | `order.delivered` | `delivered` |
| `CANCELLED` | `order.cancelled` | `cancelled` |
| `CANCELLED_IN_DELIVERY` | `order.cancelled` | `cancelled` |

**Дополнительно:** весь входящий payload логируется в таблицу `events_log` (поля: `source="market_webhook"`, `event_type`, `payload` JSON, `created_at`).

---

### `app/bot/owner_bot.py` и `app/bot/florist_bot.py` — callback handlers

Оба бота получают `CallbackQueryHandler` для `callback_data` вида:
- `ready_now:{order_id}`
- `auto_5min:{order_id}`

**Логика обработки (одинакова для обоих ботов):**

1. Читаем Redis `order:buttons:{order_id}` — если `pressed: true`, отвечаем «Уже принято» и выходим
2. Атомарно устанавливаем `pressed: true` (SET NX или GETSET)
3. `ready_now` → отменяем T55 и T57, немедленно вызываем `market_api.set_order_ready()` + polling подтверждения; при ошибке Market API — алерт владельцу, T57 уже отменён поэтому просрочку контролирует только алерт
4. `auto_5min` → просто подтверждаем выбор (T55 сработает как запланировано)
5. Редактируем все сообщения из Redis `messages` — убираем кнопки, добавляем текст «Принято: {имя}/{тип}»

---

### `app/main.py` — инициализация

```python
order_agent = OrderAgent(redis, AsyncSessionLocal, owner_bot, florist_bot, settings)
await event_bus.subscribe("order.created", order_agent.handle_order_created)
await event_bus.subscribe("order.ready", order_agent.handle_order_status)
await event_bus.subscribe("order.cancelled", order_agent.handle_order_status)
await order_agent.recover_timers()
```

---

## Поток данных: полный жизненный цикл

```
Маркет → POST /webhooks/market
    → webhooks.py: парсит статус, обновляет Order в БД, логирует payload
    → event_bus.publish("order.created", {order_id, market_order_id})

OrderAgent.handle_order_created:
    → DB: order.timer_deadline = now() + 57 min
    → Telegram: уведомление владельцу + флористам
    → asyncio.create_task × 3 (T50, T55, T57)

T+50: _run_t50
    → Telegram: сообщение + кнопки → Redis: сохранить message_ids

Кнопка нажата (owner или florist):
    → Redis: atomic set pressed=true
    → «Готов сейчас» → cancel T55, T57 → set_order_ready() → polling
    → «Авто через 5 мин» → подтверждение (T55 продолжает работать)

T+55: _run_t55
    → set_order_ready() + retry
    → polling GET каждые 40 сек × 3
    → подтверждён → publish order.ready → cancel T57
    → не подтверждён → алерт владельцу

T+57: _run_t57 (если order ещё waiting)
    → DB: status = timed_out
    → publish order.timeout
    → алерт владельцу

order.ready / order.cancelled (от Маркета):
    → OrderAgent.handle_order_status
    → cancel_timers
    → редактировать Telegram-сообщения (убрать кнопки)
```

---

## Обработка ошибок

| Ситуация | Поведение |
|---|---|
| Market API недоступен при set_order_ready | 3 retry, exponential backoff → алерт владельцу |
| Telegram недоступен при уведомлении | Логируем, не прерываем основной поток |
| `order.ready` пришёл раньше T+55 | `handle_order_status` отменяет все таймеры |
| Гонка: T+55 и `order.ready` одновременно | `_run_t57` проверяет статус в БД перед действием |
| Redis недоступен для button sync | Логируем, кнопки работают без синхронизации (оба могут нажать) |
| Флорист нажал, потом владелец | Второй получает «Уже принято» |

---

## Тесты

**Unit (без БД):**
- `_run_t50`: мокируем `asyncio.sleep` + Telegram, проверяем сохранение в Redis
- `_run_t55`: мокируем Market API, проверяем retry и polling логику
- `_run_t57`: проверяем гонку (статус уже ready → выход без действий)
- `recover_timers`: мокируем DB, проверяем что создаются только будущие задачи
- Callback handler: проверяем атомарность флага `pressed` (двойное нажатие)

**Integration (требуют PostgreSQL):**
- Полный цикл: создание заказа → T50 → кнопка → подтверждение Маркета
- Recovery: создаём заказ, «перезапускаем» агент, проверяем восстановление таймеров
- Timeout: заказ без нажатия кнопок → T57 → статус `timed_out`

---

## Затронутые файлы

- `app/agents/order_agent/agent.py` — новый
- `app/agents/order_agent/market_api.py` — новый
- `app/agents/order_agent/__init__.py` — новый
- `app/api/webhooks.py` — расширение (статус-маппинг + логирование)
- `app/bot/owner_bot.py` — callback handler
- `app/bot/florist_bot.py` — callback handler
- `app/main.py` — инициализация OrderAgent
- `tests/agents/order_agent/` — новая директория с тестами
