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
