import uuid
import pytest
from decimal import Decimal
from sqlalchemy import select

from app.models.raw_materials import RawMaterial
from app.models.market_products import MarketProduct
from app.models.recipes import Recipe
from app.models.orders import Order, OrderItem
from app.models.stock_movements import StockMovement
from app.agents.flower_stock.stock_ops import (
    reserve_materials,
    release_materials,
    debit_materials,
    compute_order_cost,
    record_arrival,
    record_spoilage,
    record_extra_debit,
    find_material_by_name,
    compute_available_stocks,
    save_order_items,
    is_eucalyptus_low,
    set_eucalyptus_stock,
)


# ─── helpers ────────────────────────────────────────────────────────────────

async def _mat(db, name=None, type_="flower", unit="шт",
               physical=Decimal("50"), reserved=Decimal("0"), cost=Decimal("80")):
    if name is None:
        name = f"Mat-{uuid.uuid4().hex[:6]}"
    m = RawMaterial(name=name, type=type_, unit=unit,
                    physical_stock=physical, reserved=reserved, cost_per_unit=cost)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m


async def _prod(db, sku=None, name="Букет"):
    if sku is None:
        sku = f"SKU-{uuid.uuid4().hex[:8]}"
    p = MarketProduct(market_sku=sku, name=name,
                      catalog_price=Decimal("500"), crossed_price=Decimal("700"),
                      min_price=Decimal("400"), optimal_price=Decimal("500"))
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return p


async def _order(db):
    o = Order(market_order_id=uuid.uuid4().hex[:10], status="waiting")
    db.add(o)
    await db.commit()
    await db.refresh(o)
    return o


# ─── reserve ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reserve_increases_reserved(db_session):
    mat = await _mat(db_session, physical=Decimal("50"), reserved=Decimal("0"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 3, "price": 500}])

    await db_session.refresh(mat)
    assert mat.reserved == Decimal("15")  # 5 * 3


@pytest.mark.asyncio
async def test_reserve_creates_movement(db_session):
    mat = await _mat(db_session, physical=Decimal("100"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("10")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 2, "price": 0}])

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.order_id == order.id,
            StockMovement.type == "reserve",
        )
    )
    movements = list(result.scalars().all())
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("20")  # 10 * 2


@pytest.mark.asyncio
async def test_reserve_skips_unknown_sku(db_session):
    order = await _order(db_session)
    # Should not raise, just skip
    await reserve_materials(db_session, order.id,
                            [{"sku": "NONEXISTENT-SKU", "count": 1, "price": 0}])


# ─── release ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_release_reduces_reserved(db_session):
    mat = await _mat(db_session, physical=Decimal("50"), reserved=Decimal("0"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 2, "price": 0}])
    await db_session.refresh(mat)
    reserved_before = mat.reserved  # 10

    await release_materials(db_session, order.id)
    await db_session.refresh(mat)
    assert mat.reserved == reserved_before - Decimal("10")


@pytest.mark.asyncio
async def test_release_creates_release_movement(db_session):
    mat = await _mat(db_session, physical=Decimal("50"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("3")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 1, "price": 0}])
    await release_materials(db_session, order.id)

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.order_id == order.id,
            StockMovement.type == "release",
        )
    )
    assert len(list(result.scalars().all())) == 1


# ─── debit ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_debit_reduces_physical_and_reserved(db_session):
    mat = await _mat(db_session, physical=Decimal("50"), reserved=Decimal("0"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 2, "price": 0}])
    await debit_materials(db_session, order.id)

    await db_session.refresh(mat)
    assert mat.physical_stock == Decimal("40")  # 50 - 10
    assert mat.reserved == Decimal("0")          # 10 - 10


@pytest.mark.asyncio
async def test_compute_order_cost_sums_debit_movements(db_session):
    mat = await _mat(db_session, physical=Decimal("50"), cost=Decimal("80"))
    prod = await _prod(db_session)
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()
    order = await _order(db_session)

    await reserve_materials(db_session, order.id,
                            [{"sku": prod.market_sku, "count": 2, "price": 0}])
    await debit_materials(db_session, order.id)

    cost = await compute_order_cost(db_session, order.id)
    assert cost == Decimal("800")  # 5 * 2 units * 80₽ = 800₽


# ─── arrival ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_arrival_increases_stock(db_session):
    mat = await _mat(db_session, physical=Decimal("10"), cost=Decimal("80"))

    updated = await record_arrival(db_session, mat.id, Decimal("30"), Decimal("60"))

    assert updated.physical_stock == Decimal("40")


@pytest.mark.asyncio
async def test_record_arrival_weighted_average_cost(db_session):
    mat = await _mat(db_session, physical=Decimal("10"), cost=Decimal("80"))

    updated = await record_arrival(db_session, mat.id, Decimal("30"), Decimal("60"))

    # weighted: (10*80 + 30*60) / 40 = 2600 / 40 = 65
    assert updated.cost_per_unit == Decimal("65")


@pytest.mark.asyncio
async def test_record_arrival_creates_movement(db_session):
    mat = await _mat(db_session, physical=Decimal("10"))

    await record_arrival(db_session, mat.id, Decimal("20"), Decimal("70"))

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.material_id == mat.id,
            StockMovement.type == "arrival",
        )
    )
    movements = list(result.scalars().all())
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("20")


# ─── spoilage ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_spoilage_reduces_physical(db_session):
    mat = await _mat(db_session, physical=Decimal("20"))

    updated = await record_spoilage(db_session, mat.id, Decimal("5"))

    assert updated.physical_stock == Decimal("15")


@pytest.mark.asyncio
async def test_record_spoilage_does_not_go_negative(db_session):
    mat = await _mat(db_session, physical=Decimal("3"))

    updated = await record_spoilage(db_session, mat.id, Decimal("10"))

    assert updated.physical_stock == Decimal("0")


# ─── extra_debit ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_record_extra_debit_creates_movement_with_note(db_session):
    mat = await _mat(db_session, physical=Decimal("30"))
    order = await _order(db_session)

    await record_extra_debit(db_session, mat.id, order.id, Decimal("3"), "сломались")

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.order_id == order.id,
            StockMovement.type == "extra_debit",
        )
    )
    movements = list(result.scalars().all())
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("3")
    assert movements[0].note == "сломались"


# ─── find_material_by_name ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_material_by_name_case_insensitive(db_session):
    suffix = uuid.uuid4().hex[:6]
    await _mat(db_session, name=f"Роза-{suffix}")

    found = await find_material_by_name(db_session, f"роза-{suffix}")
    assert found is not None
    assert suffix in found.name


@pytest.mark.asyncio
async def test_find_material_returns_none_for_unknown(db_session):
    result = await find_material_by_name(db_session, "nonexistent_xyz_123")
    assert result is None


# ─── compute_available_stocks ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_available_stocks_limiting_factor(db_session):
    sku = f"AVAIL-{uuid.uuid4().hex[:8]}"
    mat = await _mat(db_session, physical=Decimal("22"), reserved=Decimal("0"),
                     cost=Decimal("10"))
    prod = await _prod(db_session, sku=sku)
    # Recipe: 5 units per product → available = 22-0-2 = 20 → floor(20/5) = 4
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()

    stocks = await compute_available_stocks(db_session)

    assert stocks.get(sku) == 4


@pytest.mark.asyncio
async def test_compute_available_stocks_zero_when_not_enough(db_session):
    sku = f"AVAIL-{uuid.uuid4().hex[:8]}"
    mat = await _mat(db_session, physical=Decimal("2"), reserved=Decimal("0"))
    prod = await _prod(db_session, sku=sku)
    # available = 2-0-2 = 0 → 0 units
    db_session.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=Decimal("5")))
    await db_session.commit()

    stocks = await compute_available_stocks(db_session)

    assert stocks.get(sku) == 0


# ─── save_order_items ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_order_items_stores_correct_data(db_session):
    prod = await _prod(db_session)
    order = await _order(db_session)

    await save_order_items(db_session, order.id,
                           [{"sku": prod.market_sku, "count": 3, "price": 500}])

    result = await db_session.execute(
        select(OrderItem).where(OrderItem.order_id == order.id)
    )
    items = list(result.scalars().all())
    assert len(items) == 1
    assert items[0].product_id == prod.id
    assert items[0].quantity == 3
    assert items[0].unit_price == Decimal("500")


# ─── is_eucalyptus_low ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_false_when_not_found(db_session):
    # No "evkalipt" row in DB at all
    result = await is_eucalyptus_low(db_session)
    assert result is False


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_false_when_sufficient(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("500"), reserved=Decimal("100"), cost=Decimal("1"))
    # 500 - 100 = 400 >= 200 → not low
    result = await is_eucalyptus_low(db_session)
    assert result is False


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_true_below_200(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("300"), reserved=Decimal("150"), cost=Decimal("1"))
    # 300 - 150 = 150 < 200 → low
    result = await is_eucalyptus_low(db_session)
    assert result is True


@pytest.mark.asyncio
async def test_is_eucalyptus_low_returns_true_at_zero(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("0"), reserved=Decimal("0"), cost=Decimal("1"))
    result = await is_eucalyptus_low(db_session)
    assert result is True


# ─── set_eucalyptus_stock ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_set_eucalyptus_stock_sets_absolute_value(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("0"), cost=Decimal("5"))

    updated = await set_eucalyptus_stock(db_session, Decimal("400"))

    assert updated.physical_stock == Decimal("400")


@pytest.mark.asyncio
async def test_set_eucalyptus_stock_overwrites_previous_value(db_session):
    await _mat(db_session, name="evkalipt", type_="flower", unit="г",
               physical=Decimal("600"), cost=Decimal("5"))

    updated = await set_eucalyptus_stock(db_session, Decimal("200"))

    assert updated.physical_stock == Decimal("200")


@pytest.mark.asyncio
async def test_set_eucalyptus_stock_creates_arrival_movement(db_session):
    mat = await _mat(db_session, name="evkalipt", type_="flower", unit="г",
                     physical=Decimal("0"), cost=Decimal("5"))

    await set_eucalyptus_stock(db_session, Decimal("400"))

    result = await db_session.execute(
        select(StockMovement).where(
            StockMovement.material_id == mat.id,
            StockMovement.type == "arrival",
        )
    )
    movements = list(result.scalars().all())
    assert len(movements) == 1
    assert movements[0].quantity == Decimal("400")
