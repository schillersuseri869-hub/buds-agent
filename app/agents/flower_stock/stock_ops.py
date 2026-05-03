import uuid
from dataclasses import dataclass
from datetime import date, datetime
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


def _is_packaging(name: str) -> bool:
    return name.startswith("box") or name.startswith("con-kit")


async def compute_available_stocks(
    db: AsyncSession,
) -> tuple[dict[str, int], list[str]]:
    """
    Compute how many units of each active product can be sold.
    Returns (stocks, warnings):
      - stocks: {market_sku: available_count}
      - warnings: alert messages for packaging (box/con-kit) that ran out.
    Only flower materials limit the storefront count.
    Box/con-kit shortages produce a warning but do NOT remove the product.
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
    low_packaging: set[str] = set()

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
                continue
            avail = mat.available
            if _is_packaging(mat.name):
                if avail <= 0:
                    low_packaging.add(mat.name)
            else:
                counts.append(max(0, int(avail / recipe.quantity)) if avail > 0 else 0)
        stocks[product.market_sku] = min(counts) if counts else 0

    warnings = [
        f"⚠️ Упаковка «{name}» закончилась — пополните склад." for name in sorted(low_packaging)
    ]
    return stocks, warnings


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


async def record_write_off(
    db: AsyncSession, material_id: uuid.UUID, quantity: Decimal, movement_type: str
) -> RawMaterial:
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    cost = quantity * material.cost_per_unit
    material.physical_stock = max(Decimal("0"), material.physical_stock - quantity)
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type=movement_type,
        quantity=quantity,
        cost=cost,
    ))
    await db.commit()
    await db.refresh(material)
    return material


async def record_inventory_correction(
    db: AsyncSession, material_id: uuid.UUID, actual_qty: Decimal
) -> tuple[RawMaterial, Decimal]:
    result = await db.execute(
        select(RawMaterial).where(RawMaterial.id == material_id).with_for_update()
    )
    material = result.scalar_one()
    delta = actual_qty - material.physical_stock
    if delta == Decimal("0"):
        return material, delta
    cost = abs(delta) * material.cost_per_unit
    material.physical_stock = actual_qty
    db.add(StockMovement(
        material_id=material_id,
        order_id=None,
        type="inventory_correction",
        quantity=abs(delta),
        cost=cost,
        note=f"{delta:+.3f}",
    ))
    await db.commit()
    await db.refresh(material)
    return material, delta


async def get_recent_orders(db: AsyncSession, limit: int = 20) -> list[Order]:
    result = await db.execute(
        select(Order).order_by(Order.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def get_material_history(
    db: AsyncSession, material_id: uuid.UUID, limit: int = 20
) -> list[StockMovement]:
    result = await db.execute(
        select(StockMovement)
        .where(StockMovement.material_id == material_id)
        .order_by(StockMovement.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


@dataclass
class ReportData:
    arrivals_cost: Decimal
    write_offs_cost: Decimal
    current_stock_value: Decimal


async def get_report(db: AsyncSession, since: datetime) -> ReportData:
    result = await db.execute(
        select(StockMovement).where(StockMovement.created_at >= since)
    )
    movements = list(result.scalars().all())

    arrivals_cost = sum(
        (m.cost for m in movements if m.type == "arrival"), Decimal("0")
    )
    write_offs_cost = sum(
        (m.cost for m in movements if m.type in ("spoilage", "defect", "extra_debit")),
        Decimal("0"),
    )

    result = await db.execute(select(RawMaterial))
    materials = list(result.scalars().all())
    current_stock_value = sum(
        (m.physical_stock * m.cost_per_unit for m in materials), Decimal("0")
    )

    return ReportData(
        arrivals_cost=arrivals_cost,
        write_offs_cost=write_offs_cost,
        current_stock_value=current_stock_value,
    )
