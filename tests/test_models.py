import uuid
from decimal import Decimal
import pytest
from sqlalchemy import select

from app.models.raw_materials import RawMaterial
from app.models.market_products import MarketProduct
from app.models.recipes import Recipe
from app.models.florists import Florist
from app.models.orders import Order, OrderItem
from app.models.stock_movements import StockMovement
from app.models.print_jobs import PrintJob
from app.models.price_history import PriceHistory
from app.models.price_alerts import PriceAlert
from app.models.promo_participations import PromoParticipation
from app.models.economics_reports import EconomicsReport
from app.models.shop_schedule import ShopSchedule
from app.models.events_log import EventLog


@pytest.mark.asyncio
async def test_raw_material_available_calc(db_session):
    mat = RawMaterial(
        name="Роза 40см",
        type="flower",
        unit="шт",
        physical_stock=Decimal("50"),
        reserved=Decimal("30"),
        cost_per_unit=Decimal("80.00"),
    )
    db_session.add(mat)
    await db_session.commit()
    await db_session.refresh(mat)
    assert mat.available == Decimal("18")  # 50 - 30 - 2


@pytest.mark.asyncio
async def test_order_with_items(db_session):
    product = MarketProduct(
        market_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Букет из 5 роз",
        catalog_price=Decimal("1500.00"),
        crossed_price=Decimal("2100.00"),
        min_price=Decimal("1650.00"),
        optimal_price=Decimal("1800.00"),
    )
    db_session.add(product)
    await db_session.flush()

    order = Order(
        market_order_id=f"YM-{uuid.uuid4().hex[:8]}",
        sale_price=Decimal("1500.00"),
        estimated_commission_pct=Decimal("15.00"),
    )
    db_session.add(order)
    await db_session.flush()

    item = OrderItem(
        order_id=order.id,
        product_id=product.id,
        quantity=1,
        unit_price=Decimal("1500.00"),
    )
    db_session.add(item)
    await db_session.commit()

    result = await db_session.execute(select(Order).where(Order.id == order.id))
    saved = result.scalar_one()
    assert saved.status == "waiting"
    assert saved.sale_price == Decimal("1500.00")


@pytest.mark.asyncio
async def test_stock_movement_types(db_session):
    mat = RawMaterial(
        name="Хризантема",
        type="flower",
        unit="шт",
        physical_stock=Decimal("20"),
        reserved=Decimal("0"),
        cost_per_unit=Decimal("50.00"),
    )
    db_session.add(mat)
    await db_session.flush()

    movement = StockMovement(
        material_id=mat.id,
        type="arrival",
        quantity=Decimal("20"),
        cost=Decimal("1000.00"),
    )
    db_session.add(movement)
    await db_session.commit()
    assert movement.id is not None


@pytest.mark.asyncio
async def test_events_log(db_session):
    log = EventLog(
        event_type="order.created",
        payload={"order_id": "YM-999"},
    )
    db_session.add(log)
    await db_session.commit()
    assert log.id is not None


@pytest.mark.asyncio
async def test_recipe_links_product_and_material(db_session):
    product = MarketProduct(
        market_sku=f"SKU-{uuid.uuid4().hex[:8]}",
        name="Тест-букет",
        catalog_price=Decimal("500"),
        crossed_price=Decimal("700"),
        min_price=Decimal("550"),
        optimal_price=Decimal("600"),
    )
    material = RawMaterial(
        name="Тюльпан",
        type="flower",
        unit="шт",
        physical_stock=Decimal("100"),
        reserved=Decimal("0"),
        cost_per_unit=Decimal("40"),
    )
    db_session.add_all([product, material])
    await db_session.flush()

    recipe = Recipe(product_id=product.id, material_id=material.id, quantity=Decimal("5"))
    db_session.add(recipe)
    await db_session.commit()
    assert recipe.id is not None


@pytest.mark.asyncio
async def test_market_product_has_is_pr(db_session):
    prod = MarketProduct(
        market_sku="TEST-PR-001",
        name="Test PR SKU",
        catalog_price=Decimal("1900"),
        crossed_price=Decimal("2660"),
        min_price=Decimal("1000"),
        optimal_price=Decimal("1000"),
        is_pr=True,
    )
    db_session.add(prod)
    await db_session.commit()
    await db_session.refresh(prod)
    assert prod.is_pr is True


@pytest.mark.asyncio
async def test_price_history_has_promo_price(db_session):
    ph = PriceHistory(
        product_id=uuid.uuid4(),
        catalog_price=Decimal("1500"),
        storefront_price=Decimal("1200"),
        min_price=Decimal("1000"),
        optimal_price=Decimal("1000"),
        promo_price=Decimal("1100"),
    )
    db_session.add(ph)
    await db_session.commit()
    await db_session.refresh(ph)
    assert ph.promo_price == Decimal("1100")


@pytest.mark.asyncio
async def test_promo_participation_create(db_session):
    from datetime import datetime, timezone
    pp = PromoParticipation(
        product_id=uuid.uuid4(),
        promo_id="promo-abc-123",
        promo_type="direct_discount",
        promo_price=Decimal("1100"),
        discount_pct=None,
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(pp)
    await db_session.commit()
    await db_session.refresh(pp)
    assert pp.promo_id == "promo-abc-123"


@pytest.mark.asyncio
async def test_promo_create(db_session):
    from datetime import datetime, timezone
    from app.models.promo import Promo

    promo = Promo(
        promo_id="PROMO-XYZ-001",
        name="Скидка мая",
        type="DIRECT_DISCOUNT",
        starts_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        ends_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        updated_at=datetime(2026, 5, 4, tzinfo=timezone.utc),
    )
    db_session.add(promo)
    await db_session.commit()
    await db_session.refresh(promo)

    assert promo.promo_id == "PROMO-XYZ-001"
    assert promo.type == "DIRECT_DISCOUNT"
    assert promo.ends_at.year == 2026
