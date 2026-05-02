import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_products import MarketProduct
from app.models.orders import Order, OrderItem
from app.models.raw_materials import RawMaterial
from app.models.recipes import Recipe
from app.models.stock_movements import StockMovement


async def save_order_items(
    db: AsyncSession, order_id: uuid.UUID, items: list[dict]
) -> None:
    """Persist order items from Market API response. items: [{sku, count, price}]"""
    for item in items:
        result = await db.execute(
            select(MarketProduct).where(MarketProduct.market_sku == item["sku"])
        )
        product = result.scalar_one_or_none()
        if product is None:
            continue
        db.add(OrderItem(
            order_id=order_id,
            product_id=product.id,
            quantity=item["count"],
            unit_price=Decimal(str(item.get("price", 0))),
        ))
    await db.commit()


async def reserve_materials(
    db: AsyncSession, order_id: uuid.UUID, items: list[dict]
) -> None:
    """Reserve raw materials for order items. items: [{sku, count, price}]"""
    for item in items:
        result = await db.execute(
            select(MarketProduct).where(MarketProduct.market_sku == item["sku"])
        )
        product = result.scalar_one_or_none()
        if product is None:
            continue

        result = await db.execute(
            select(Recipe).where(Recipe.product_id == product.id)
        )
        recipes = list(result.scalars().all())

        for recipe in recipes:
            qty = recipe.quantity * Decimal(str(item["count"]))
            result = await db.execute(
                select(RawMaterial)
                .where(RawMaterial.id == recipe.material_id)
                .with_for_update()
            )
            material = result.scalar_one_or_none()
            if material is None:
                continue
            material.reserved += qty
            db.add(StockMovement(
                material_id=material.id,
                order_id=order_id,
                type="reserve",
                quantity=qty,
                cost=qty * material.cost_per_unit,
            ))

    await db.commit()


async def release_materials(db: AsyncSession, order_id: uuid.UUID) -> None:
    """Release reserved materials (on cancel/timeout)."""
    result = await db.execute(
        select(StockMovement).where(
            StockMovement.order_id == order_id,
            StockMovement.type == "reserve",
        )
    )
    reserve_movements = list(result.scalars().all())

    for movement in reserve_movements:
        result = await db.execute(
            select(RawMaterial)
            .where(RawMaterial.id == movement.material_id)
            .with_for_update()
        )
        material = result.scalar_one_or_none()
        if material is None:
            continue
        material.reserved = max(Decimal("0"), material.reserved - movement.quantity)
        db.add(StockMovement(
            material_id=material.id,
            order_id=order_id,
            type="release",
            quantity=movement.quantity,
            cost=movement.cost,
        ))

    await db.commit()


async def debit_materials(db: AsyncSession, order_id: uuid.UUID) -> None:
    """Debit materials on order.ready: reduce physical_stock and reserved."""
    result = await db.execute(
        select(StockMovement).where(
            StockMovement.order_id == order_id,
            StockMovement.type == "reserve",
        )
    )
    reserve_movements = list(result.scalars().all())

    for movement in reserve_movements:
        result = await db.execute(
            select(RawMaterial)
            .where(RawMaterial.id == movement.material_id)
            .with_for_update()
        )
        material = result.scalar_one_or_none()
        if material is None:
            continue
        material.physical_stock = max(Decimal("0"), material.physical_stock - movement.quantity)
        material.reserved = max(Decimal("0"), material.reserved - movement.quantity)
        db.add(StockMovement(
            material_id=material.id,
            order_id=order_id,
            type="debit",
            quantity=movement.quantity,
            cost=movement.cost,
        ))

    await db.commit()


async def compute_order_cost(db: AsyncSession, order_id: uuid.UUID) -> Decimal:
    """Sum debit + extra_debit movement costs for this order."""
    result = await db.execute(
        select(StockMovement).where(
            StockMovement.order_id == order_id,
            StockMovement.type.in_(["debit", "extra_debit"]),
        )
    )
    return sum((m.cost for m in result.scalars().all()), Decimal("0"))


async def record_arrival(
    db: AsyncSession, material_id: uuid.UUID, quantity: Decimal, cost_per_unit: Decimal
) -> RawMaterial:
    """Record material arrival, recalculate weighted average cost."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    new_total = material.physical_stock + quantity
    if new_total > 0:
        material.cost_per_unit = (
            material.physical_stock * material.cost_per_unit + quantity * cost_per_unit
        ) / new_total
    material.physical_stock += quantity
    material.last_delivery_date = date.today()
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type="arrival",
        quantity=quantity,
        cost=quantity * cost_per_unit,
    ))
    await db.commit()
    await db.refresh(material)
    return material


async def record_spoilage(
    db: AsyncSession, material_id: uuid.UUID, quantity: Decimal
) -> RawMaterial:
    """Record spoilage write-off."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    cost = quantity * material.cost_per_unit
    material.physical_stock = max(Decimal("0"), material.physical_stock - quantity)
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type="spoilage",
        quantity=quantity,
        cost=cost,
    ))
    await db.commit()
    await db.refresh(material)
    return material


async def record_extra_debit(
    db: AsyncSession,
    material_id: uuid.UUID,
    order_id: uuid.UUID,
    quantity: Decimal,
    note: str,
) -> RawMaterial:
    """Record extra material write-off during assembly."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    cost = quantity * material.cost_per_unit
    material.physical_stock = max(Decimal("0"), material.physical_stock - quantity)
    db.add(StockMovement(
        material_id=material_id,
        order_id=order_id,
        type="extra_debit",
        quantity=quantity,
        cost=cost,
        note=note,
    ))
    await db.commit()
    await db.refresh(material)
    return material


async def find_material_by_name(db: AsyncSession, name: str) -> RawMaterial | None:
    """Case-insensitive partial search for a material by name."""
    result = await db.execute(
        select(RawMaterial)
        .where(RawMaterial.name.ilike(f"%{name}%"))
        .limit(1)
    )
    return result.scalar_one_or_none()


async def compute_available_stocks(db: AsyncSession) -> dict[str, int]:
    """
    Compute how many units of each active product can be sold.
    Returns {market_sku: available_count} for all active products.
    available = physical_stock − reserved − 2  (per RawMaterial.available property)
    """
    result = await db.execute(select(RawMaterial))
    materials = {m.id: m for m in result.scalars().all()}

    result = await db.execute(select(Recipe))
    recipes: dict[uuid.UUID, list[Recipe]] = {}
    for recipe in result.scalars().all():
        recipes.setdefault(recipe.product_id, []).append(recipe)

    result = await db.execute(
        select(MarketProduct).where(MarketProduct.status == "active")
    )

    stocks: dict[str, int] = {}
    for product in result.scalars().all():
        product_recipes = recipes.get(product.id, [])
        if not product_recipes:
            stocks[product.market_sku] = 0
            continue
        counts = []
        for recipe in product_recipes:
            mat = materials.get(recipe.material_id)
            if mat is None or recipe.quantity <= 0:
                counts.append(0)
            else:
                avail = mat.available
                counts.append(max(0, int(avail / recipe.quantity)) if avail > 0 else 0)
        stocks[product.market_sku] = min(counts) if counts else 0

    return stocks


async def is_eucalyptus_low(db: AsyncSession) -> bool:
    """True if net eucalyptus (physical_stock - reserved) is below 200g."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.name == "evkalipt")
    )
    mat = result.scalar_one_or_none()
    if mat is None:
        return False
    return (mat.physical_stock - mat.reserved) < Decimal("200")


async def set_eucalyptus_stock(db: AsyncSession, quantity: Decimal) -> RawMaterial:
    """Set eucalyptus physical_stock to an absolute value (florist's count).
    Logs a StockMovement type='arrival' with the reported total (manual correction)."""
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.name == "evkalipt").with_for_update()
    )
    mat = result.scalar_one()
    mat.physical_stock = quantity
    db.add(StockMovement(
        material_id=mat.id,
        order_id=None,
        type="arrival",
        quantity=quantity,
        cost=quantity * mat.cost_per_unit,
    ))
    await db.commit()
    await db.refresh(mat)
    return mat
