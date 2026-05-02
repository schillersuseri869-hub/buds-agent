import uuid
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.flower_stock.agent import FlowerStockAgent


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


# ─── handle_order_created ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_order_created_calls_reserve_and_update_storefront():
    fake_items = [{"sku": "SKU-1", "count": 2, "price": 500}]

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi, \
         patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_mapi.get_order_items = AsyncMock(return_value=fake_items)
        mock_ops.save_order_items = AsyncMock()
        mock_ops.reserve_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={"SKU-1": 8})
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent()
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "MKT-001",
        })

    mock_mapi.get_order_items.assert_awaited_once()
    mock_ops.save_order_items.assert_awaited_once()
    mock_ops.reserve_materials.assert_awaited_once()
    mock_mapi.update_stocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_order_created_alerts_on_api_error():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    agent = _make_agent(owner_bot=owner_bot)

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi:
        mock_mapi.get_order_items = AsyncMock(side_effect=Exception("API down"))
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "X",
        })

    owner_bot.send_message.assert_awaited()


@pytest.mark.asyncio
async def test_handle_order_created_ignores_missing_fields():
    agent = _make_agent()
    await agent.handle_order_created("order.created", {})
    await agent.handle_order_created("order.created", {"order_id": "abc"})


# ─── handle_order_ready ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_order_ready_calls_debit_and_update_storefront():
    order_mock = MagicMock()
    order_mock.estimated_cost = None
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=order_mock))
    )
    session_mock.commit = AsyncMock()

    with patch("app.agents.flower_stock.agent.stock_ops") as mock_ops, \
         patch("app.agents.flower_stock.agent.market_api") as mock_mapi:
        mock_ops.debit_materials = AsyncMock()
        mock_ops.compute_order_cost = AsyncMock(return_value=Decimal("120"))
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent(db_factory=MagicMock(return_value=session_mock))
        await agent.handle_order_ready("order.ready", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "M-1",
        })

    mock_ops.debit_materials.assert_awaited_once()
    mock_ops.compute_order_cost.assert_awaited_once()
    mock_mapi.update_stocks.assert_awaited_once()


# ─── handle_order_released ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_order_released_calls_release_and_update_storefront():
    with patch("app.agents.flower_stock.agent.stock_ops") as mock_ops, \
         patch("app.agents.flower_stock.agent.market_api") as mock_mapi:
        mock_ops.release_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent()
        await agent.handle_order_released("order.cancelled", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "M-2",
        })

    mock_ops.release_materials.assert_awaited_once()
    mock_mapi.update_stocks.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_order_released_ignores_missing_order_id():
    agent = _make_agent()
    await agent.handle_order_released("order.cancelled", {})


# ─── _parse_command ──────────────────────────────────────────────────────────

def test_parse_arrival_command():
    agent = _make_agent()
    result = agent._parse_command("пришло 30 роз 40см по 80₽")
    assert result is not None
    assert result["type"] == "arrival"
    assert result["quantity"] == Decimal("30")
    assert "роз 40см" in result["material_name"]
    assert result["cost_per_unit"] == Decimal("80")


def test_parse_arrival_command_with_comma():
    agent = _make_agent()
    result = agent._parse_command("пришло 10,5 хризантем по 45,50₽")
    assert result is not None
    assert result["quantity"] == Decimal("10.5")
    assert result["cost_per_unit"] == Decimal("45.50")


def test_parse_spoilage_command():
    agent = _make_agent()
    result = agent._parse_command("списал 5 хризантем белых")
    assert result is not None
    assert result["type"] == "spoilage"
    assert result["quantity"] == Decimal("5")
    assert "хризантем белых" in result["material_name"]


def test_parse_extra_debit_command():
    agent = _make_agent()
    result = agent._parse_command("дополнительно списал 3 розы к заказу #MKT-123")
    assert result is not None
    assert result["type"] == "extra_debit"
    assert result["quantity"] == Decimal("3")
    assert result["order_ref"] == "MKT-123"


def test_parse_unrecognized_returns_none():
    agent = _make_agent()
    assert agent._parse_command("открой магазин") is None
    assert agent._parse_command("статус") is None
    assert agent._parse_command("") is None


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


@pytest.mark.asyncio
async def test_alert_all_skips_florist_when_telegram_id_missing():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    florist_bot = AsyncMock()
    florist_bot.send_message = AsyncMock()
    settings = MagicMock()
    settings.owner_telegram_id = 111111
    settings.florist_telegram_id = None  # not configured
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
    florist_bot.send_message.assert_not_awaited()


# ─── eucalyptus check in handle_order_created ───────────────────────────────

@pytest.mark.asyncio
async def test_handle_order_created_sends_alert_when_eucalyptus_low():
    owner_bot = AsyncMock()
    owner_bot.send_message = AsyncMock()
    florist_bot = AsyncMock()
    florist_bot.send_message = AsyncMock()
    fake_items = [{"sku": "bouquet-e-red", "count": 1, "price": 500}]

    with patch("app.agents.flower_stock.agent.market_api") as mock_mapi, \
         patch("app.agents.flower_stock.agent.stock_ops") as mock_ops:
        mock_mapi.get_order_items = AsyncMock(return_value=fake_items)
        mock_ops.save_order_items = AsyncMock()
        mock_ops.reserve_materials = AsyncMock()
        mock_ops.compute_available_stocks = AsyncMock(return_value={})
        mock_ops.is_eucalyptus_low = AsyncMock(return_value=True)
        mock_mapi.update_stocks = AsyncMock()

        agent = _make_agent(owner_bot=owner_bot, florist_bot=florist_bot)
        await agent.handle_order_created("order.created", {
            "order_id": str(uuid.uuid4()),
            "market_order_id": "MKT-E-001",
        })

    # owner gets plain text alert (no keyboard)
    owner_texts = [str(call) for call in owner_bot.send_message.call_args_list]
    assert any("Эвкалипт" in t for t in owner_texts)
    # florist gets the keyboard
    florist_bot.send_message.assert_awaited_once()
    florist_call_kwargs = florist_bot.send_message.call_args
    assert "reply_markup" in florist_call_kwargs.kwargs


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
