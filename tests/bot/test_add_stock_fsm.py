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
