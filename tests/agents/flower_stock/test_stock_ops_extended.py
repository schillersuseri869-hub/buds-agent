import uuid
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.flower_stock import stock_ops


def _make_session():
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.add = MagicMock()
    return session


def _make_material(physical_stock="50", reserved="0", cost_per_unit="80"):
    m = MagicMock()
    m.id = uuid.uuid4()
    m.name = "Роза 40см"
    m.unit = "шт."
    m.physical_stock = Decimal(physical_stock)
    m.reserved = Decimal(reserved)
    m.cost_per_unit = Decimal(cost_per_unit)
    return m


# ── record_write_off ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_write_off_defect_reduces_stock():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result = await stock_ops.record_write_off(session, mat.id, Decimal("3"), "defect")

    assert mat.physical_stock == Decimal("47")
    added = session.add.call_args[0][0]
    assert added.type == "defect"
    assert added.quantity == Decimal("3")


@pytest.mark.asyncio
async def test_record_write_off_does_not_go_below_zero():
    mat = _make_material(physical_stock="2")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    await stock_ops.record_write_off(session, mat.id, Decimal("10"), "spoilage")

    assert mat.physical_stock == Decimal("0")


# ── record_inventory_correction ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_inventory_correction_positive_delta():
    mat = _make_material(physical_stock="40")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("47"))

    assert mat.physical_stock == Decimal("47")
    assert delta == Decimal("7")
    added = session.add.call_args[0][0]
    assert added.type == "inventory_correction"
    assert added.quantity == Decimal("7")


@pytest.mark.asyncio
async def test_record_inventory_correction_negative_delta():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("43"))

    assert mat.physical_stock == Decimal("43")
    assert delta == Decimal("-7")
    added = session.add.call_args[0][0]
    assert added.quantity == Decimal("7")  # stored as abs


@pytest.mark.asyncio
async def test_record_inventory_correction_no_change_records_nothing():
    mat = _make_material(physical_stock="50")
    session = _make_session()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one=MagicMock(return_value=mat))
    )
    session.refresh = AsyncMock(side_effect=lambda m: None)

    result_mat, delta = await stock_ops.record_inventory_correction(session, mat.id, Decimal("50"))

    assert delta == Decimal("0")
    session.add.assert_not_called()


# ── get_recent_orders ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_recent_orders_returns_list():
    session = _make_session()
    orders = [MagicMock(), MagicMock()]
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=orders))))
    )
    result = await stock_ops.get_recent_orders(session, limit=20)
    assert result == orders


# ── get_material_history ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_material_history_returns_list():
    session = _make_session()
    movements = [MagicMock(), MagicMock(), MagicMock()]
    session.execute = AsyncMock(
        return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=movements))))
    )
    result = await stock_ops.get_material_history(session, uuid.uuid4(), limit=20)
    assert result == movements


# ── get_report ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_report_sums_correctly():
    from app.models.stock_movements import StockMovement
    from app.models.raw_materials import RawMaterial

    session = _make_session()

    mov1 = MagicMock(); mov1.type = "arrival"; mov1.cost = Decimal("500")
    mov2 = MagicMock(); mov2.type = "spoilage"; mov2.cost = Decimal("80")
    mov3 = MagicMock(); mov3.type = "defect"; mov3.cost = Decimal("40")
    mat1 = MagicMock(); mat1.physical_stock = Decimal("10"); mat1.cost_per_unit = Decimal("100")

    call_count = 0
    async def mock_execute(stmt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mov1, mov2, mov3]))))
        return MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mat1]))))

    session.execute = mock_execute
    since = datetime.now(timezone.utc) - timedelta(days=7)
    report = await stock_ops.get_report(session, since)

    assert report.arrivals_cost == Decimal("500")
    assert report.write_offs_cost == Decimal("120")  # 80 + 40
    assert report.current_stock_value == Decimal("1000")  # 10 * 100
