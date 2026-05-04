import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.pricing_agent.agent import PricingAgent


def _make_session_mock(products=None):
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.execute = AsyncMock(return_value=MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=products or [])))
    ))
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()
    return session_mock


def _make_agent(db_factory=None, owner_bot=None, settings=None, scheduler=None, products=None):
    if db_factory is None:
        session_mock = _make_session_mock(products)
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
    fake_product = MagicMock()
    fake_product.market_sku = "SKU-001"
    fake_product.id = "uuid-001"
    agent = _make_agent(products=[fake_product])

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


@pytest.mark.asyncio
async def test_sync_promos_calls_merge_for_each_promo():
    from app.models.promo import Promo

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.merge = MagicMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    agent = _make_agent(db_factory=db_factory)

    promos = [
        {
            "id": "promo-aaa",
            "name": "Майская акция",
            "mechanicsType": "DIRECT_DISCOUNT",
            "startDate": "2026-05-01",
            "endDate": "2026-05-31",
        },
        {
            "promoId": "promo-bbb",
            "name": "Фиксированная цена",
            "mechanicsType": "FIXED_PRICE",
            "startDate": None,
            "endDate": None,
        },
    ]

    await agent._sync_promos(promos)

    assert session_mock.merge.call_count == 2
    first_arg = session_mock.merge.call_args_list[0].args[0]
    assert isinstance(first_arg, Promo)
    assert first_arg.promo_id == "promo-aaa"
    assert first_arg.name == "Майская акция"
    assert first_arg.type == "DIRECT_DISCOUNT"
    second_arg = session_mock.merge.call_args_list[1].args[0]
    assert second_arg.promo_id == "promo-bbb"


@pytest.mark.asyncio
async def test_update_storefront_prices_sets_field():
    from decimal import Decimal
    from app.agents.pricing_agent.market_api import PricesReport

    prod = MagicMock()
    prod.market_sku = "SKU-001"
    prod.storefront_price = None

    prod_no_price = MagicMock()
    prod_no_price.market_sku = "SKU-002"
    prod_no_price.storefront_price = None

    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock(return_value=False)
    session_mock.add = MagicMock()
    session_mock.commit = AsyncMock()
    db_factory = MagicMock(return_value=session_mock)

    agent = _make_agent(db_factory=db_factory)

    report = PricesReport(storefront={"SKU-001": Decimal("1200")})
    await agent._update_storefront_prices([prod, prod_no_price], report)

    assert prod.storefront_price == Decimal("1200")
    assert prod_no_price.storefront_price is None  # not in report — don't touch
    session_mock.add.assert_called_once_with(prod)
    session_mock.commit.assert_called_once()
