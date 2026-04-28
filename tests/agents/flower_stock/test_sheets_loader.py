import pytest
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

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct, \
         patch("app.agents.flower_stock.sheets_loader.select"):
        MockProduct.return_value = MagicMock()
        await load_products(session, rows)

    assert MockProduct.call_args.kwargs["is_pr"] is True


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

    with patch("app.agents.flower_stock.sheets_loader.MarketProduct") as MockProduct, \
         patch("app.agents.flower_stock.sheets_loader.select"):
        MockProduct.return_value = MagicMock()
        await load_products(session, rows)

    assert MockProduct.call_args.kwargs["is_pr"] is False
