import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from app.agents.flower_stock.sheets_loader import load_products


@pytest.mark.asyncio
async def test_load_products_sets_is_pr_true():
    rows = [
        {"market_sku": "SKU-001", "name": "Роза красная", "catalog_price": 1500,
         "crossed_price": 2100, "min_price": 1000, "optimal_price": 1000, "is_pr": True},
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct, \
         patch("app.agents.flower_stock.sheets_loader.select"):
        MockProduct.return_value = MagicMock()
        await load_products(session, rows)

    assert MockProduct.call_args.kwargs["is_pr"] is True


@pytest.mark.asyncio
async def test_load_products_sets_is_pr_false_by_default():
    rows = [
        {"market_sku": "SKU-002", "name": "Тюльпан", "catalog_price": 750,
         "crossed_price": 1050, "min_price": 500, "optimal_price": 500, "is_pr": False},
    ]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct, \
         patch("app.agents.flower_stock.sheets_loader.select"):
        MockProduct.return_value = MagicMock()
        await load_products(session, rows)

    assert MockProduct.call_args.kwargs["is_pr"] is False
