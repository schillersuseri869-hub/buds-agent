import json
import uuid
import pytest
import fakeredis.aioredis
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── Task 2: _alert, _notify_all ──────────────────────────────────────────────

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
    await agent._alert("test")


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


# ── Task 3: _run_t50 ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_t50_sends_buttons_and_saves_to_redis():
    redis = fakeredis.aioredis.FakeRedis()
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock(return_value=MagicMock(message_id=77))
    agent = make_agent(redis=redis, owner_bot=owner_bot)

    order_id = str(uuid.uuid4())
    agent._tasks[order_id] = []

    fire_at = datetime.now(timezone.utc)

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t50(order_id, "YM-999", fire_at)

    owner_bot.send_message.assert_awaited_once()
    call_kwargs = owner_bot.send_message.call_args[1]
    assert call_kwargs["reply_markup"] is not None

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

    with patch("app.agents.order_agent.agent._sleep_until", new_callable=AsyncMock):
        await agent._run_t50(order_id, "YM-999", datetime.now(timezone.utc))

    agent._owner_bot.send_message.assert_not_awaited()


# ── Task 4: _run_t55 ─────────────────────────────────────────────────────────

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


# ── Task 5: _run_t57, _schedule_timers, handle_order_created ─────────────────

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


# ── Task 6: cancel_timers, handle_order_status, _clear_button_messages ────────

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
    agent.cancel_timers("nonexistent-id")


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


# ── Task 7: recover_timers ───────────────────────────────────────────────────

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


# ── Task 8: handle_button_callback ───────────────────────────────────────────

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
    await redis.set(f"order:buttons:pressed:{order_id}", "1", ex=7200)

    callback = AsyncMock()
    callback.data = f"ready_now:{order_id}"
    callback.answer = AsyncMock()

    with patch("app.agents.order_agent.agent.market_api.set_order_ready",
               new_callable=AsyncMock) as mock_api:
        await agent.handle_button_callback(callback)

    mock_api.assert_not_awaited()
    call_text = callback.answer.call_args[0][0]
    assert "Уже" in call_text
