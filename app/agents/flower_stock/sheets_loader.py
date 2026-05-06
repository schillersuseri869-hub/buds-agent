import logging
import uuid
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_products import MarketProduct
from app.models.raw_materials import RawMaterial
from app.models.recipes import Recipe
from app.models.stock_movements import StockMovement

logger = logging.getLogger(__name__)


def _d(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        return Decimal("0")


def _d_or_none(value) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        return None


async def _fetch_table(base_url: str, doc_id: str, api_key: str, table: str) -> list[dict]:
    url = f"{base_url}/api/docs/{doc_id}/tables/{table}/records"
    async with httpx.AsyncClient() as client:
        response = await client.get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30.0,
        )
        response.raise_for_status()
    return [{**r["fields"], "_grist_id": r["id"]} for r in response.json().get("records", [])]


async def push_material_to_grist(
    base_url: str, doc_id: str, api_key: str, row_id: int,
    physical_stock: Decimal, reserved: Decimal = Decimal("0"),
    min_buffer: Decimal = Decimal("0"),
) -> None:
    """PATCH a single Materials row in Grist with stock values."""
    available = float(max(Decimal("0"), physical_stock - reserved - min_buffer))
    url = f"{base_url}/api/docs/{doc_id}/tables/Materials/records"
    payload = {"records": [{"id": row_id, "fields": {
        "physical_stock": float(physical_stock),
        "reserved": float(reserved),
        "available": available,
    }}]}
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()


async def push_materials_status_to_grist(
    base_url: str, doc_id: str, api_key: str, db: "AsyncSession"
) -> None:
    """Update reserved + available columns in Grist Materials table from PG state."""
    from sqlalchemy import select as _select
    from app.models.raw_materials import RawMaterial

    result = await db.execute(
        _select(RawMaterial).where(RawMaterial.grist_row_id.isnot(None))
    )
    materials = list(result.scalars().all())

    records = [
        {
            "id": m.grist_row_id,
            "fields": {
                "reserved": float(m.reserved),
                "available": float(max(
                    Decimal("0"),
                    m.physical_stock - m.reserved - (m.min_buffer or Decimal("0"))
                )),
            },
        }
        for m in materials
    ]
    if not records:
        return

    url = f"{base_url}/api/docs/{doc_id}/tables/Materials/records"
    async with httpx.AsyncClient() as client:
        response = await client.patch(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"records": records},
            timeout=15.0,
        )
        response.raise_for_status()


async def push_stock_movement_to_grist(
    base_url: str,
    doc_id: str,
    api_key: str,
    material_name: str,
    movement_type: str,
    quantity: Decimal,
    order_id: str | None,
) -> None:
    """Append one row to StockMovements table."""
    from datetime import datetime, timezone
    now_ts = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "records": [{
            "fields": {
                "date": ["d", now_ts],
                "material": material_name,
                "type": movement_type,
                "quantity": float(quantity),
                "order_id": order_id or "",
            }
        }]
    }
    url = f"{base_url}/api/docs/{doc_id}/tables/StockMovements/records"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()


async def load_materials(db: AsyncSession, rows: list[dict]) -> dict[str, RawMaterial]:
    loaded: dict[str, RawMaterial] = {}
    for row in rows:
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        type_ = str(row.get("type", "")).strip()
        unit = str(row.get("unit", "")).strip()
        physical_stock = _d(row.get("physical_stock", 0))
        cost_per_unit = _d(row.get("cost_per_unit", 0))
        grist_row_id = row.get("_grist_id")
        min_stock = _d_or_none(row.get("min_stock"))
        min_buffer = _d(row.get("min_buffer", 0))

        result = await db.execute(select(RawMaterial).where(RawMaterial.name == name))
        mat = result.scalar_one_or_none()
        if mat is None:
            mat = RawMaterial(
                name=name, type=type_, unit=unit,
                physical_stock=physical_stock, cost_per_unit=cost_per_unit,
                grist_row_id=grist_row_id, min_stock=min_stock,
                min_buffer=min_buffer,
            )
            db.add(mat)
        else:
            mat.type = type_
            mat.unit = unit
            mat.physical_stock = physical_stock
            mat.cost_per_unit = cost_per_unit
            mat.grist_row_id = grist_row_id
            mat.min_stock = min_stock
            mat.min_buffer = min_buffer
        await db.commit()
        await db.refresh(mat)
        loaded[name] = mat
        logger.info("Loaded material: %s (grist_row_id=%s, min_stock=%s, min_buffer=%s)", name, grist_row_id, min_stock, min_buffer)
    return loaded


async def load_products(db: AsyncSession, rows: list[dict]) -> dict[str, MarketProduct]:
    loaded: dict[str, MarketProduct] = {}
    for row in rows:
        sku = str(row.get("market_sku", "")).strip()
        if not sku:
            continue
        name = str(row.get("name", "")).strip()
        catalog_price = _d(row.get("catalog_price", 0))
        crossed_price = _d(row.get("crossed_price", 0))
        min_price = _d(row.get("min_price", 0))
        optimal_price = _d(row.get("optimal_price", 0))
        is_pr = bool(row.get("is_pr", False))

        result = await db.execute(select(MarketProduct).where(MarketProduct.market_sku == sku))
        prod = result.scalar_one_or_none()
        if prod is None:
            prod = MarketProduct(
                market_sku=sku, name=name,
                catalog_price=catalog_price, crossed_price=crossed_price,
                min_price=min_price, optimal_price=optimal_price,
                is_pr=is_pr,
            )
            db.add(prod)
        else:
            prod.name = name
            prod.catalog_price = catalog_price
            prod.crossed_price = crossed_price
            prod.min_price = min_price
            prod.optimal_price = optimal_price
            prod.is_pr = is_pr
        await db.commit()
        await db.refresh(prod)
        loaded[sku] = prod
        logger.info("Loaded product: %s — %s (is_pr=%s)", sku, name, is_pr)
    return loaded


async def load_recipes(
    db: AsyncSession,
    rows: list[dict],
    products: dict[str, MarketProduct],
    materials: dict[str, RawMaterial],
) -> int:
    count = 0
    for row in rows:
        sku = str(row.get("market_sku", "")).strip()
        mat_name = str(row.get("material_name", "")).strip()
        qty = _d(row.get("quantity", 0))
        if not sku or not mat_name:
            continue

        prod = products.get(sku)
        mat = materials.get(mat_name)
        if prod is None:
            logger.warning("Recipe references unknown SKU: %s", sku)
            continue
        if mat is None:
            logger.warning("Recipe references unknown material: %s", mat_name)
            continue

        result = await db.execute(
            select(Recipe).where(Recipe.product_id == prod.id, Recipe.material_id == mat.id)
        )
        recipe = result.scalar_one_or_none()
        if recipe is None:
            db.add(Recipe(product_id=prod.id, material_id=mat.id, quantity=qty))
        else:
            recipe.quantity = qty
        await db.commit()
        count += 1
    logger.info("Loaded %d recipes", count)
    return count


_WRITE_OFF_TYPE_LABELS = {
    "defect": "Брак",
    "spoilage": "Порча",
    "extra_debit": "К заказу",
}


async def push_write_off_to_grist(
    base_url: str,
    doc_id: str,
    api_key: str,
    material_name: str,
    wo_type: str,
    quantity: Decimal,
    unit: str,
    cost_per_unit: Decimal,
) -> None:
    """Append a row to the WriteOffs table in Grist."""
    from datetime import datetime, timezone
    total_cost = quantity * cost_per_unit
    now_ts = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "records": [{
            "fields": {
                "date": ["d", now_ts],
                "material": material_name,
                "type": _WRITE_OFF_TYPE_LABELS.get(wo_type, wo_type),
                "quantity": float(quantity),
                "unit": unit,
                "cost_per_unit": float(cost_per_unit),
                "total_cost": float(total_cost),
            }
        }]
    }
    url = f"{base_url}/api/docs/{doc_id}/tables/WriteOffs/records"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()


async def load_from_grist(
    db: AsyncSession,
    base_url: str,
    doc_id: str,
    api_key: str,
) -> tuple[int, int]:
    """Load all data from Grist into DB (upsert). Returns (n_materials, n_products)."""
    mat_rows = await _fetch_table(base_url, doc_id, api_key, "Materials")
    prod_rows = await _fetch_table(base_url, doc_id, api_key, "Products")
    recipe_rows = await _fetch_table(base_url, doc_id, api_key, "Recipes")

    materials = await load_materials(db, mat_rows)
    products = await load_products(db, prod_rows)
    await load_recipes(db, recipe_rows, products, materials)

    # Nullify grist_row_id for PG records no longer present in Grist
    active_mat_names = set(materials.keys())
    result = await db.execute(select(RawMaterial))
    for mat in result.scalars().all():
        if mat.name not in active_mat_names and mat.grist_row_id is not None:
            mat.grist_row_id = None
    active_sku = set(products.keys())
    result = await db.execute(select(MarketProduct))
    for prod in result.scalars().all():
        if prod.market_sku not in active_sku and prod.grist_row_id is not None:
            prod.grist_row_id = None
    await db.commit()

    logger.info(
        "Grist load complete: %d materials, %d products",
        len(materials), len(products),
    )
    return len(materials), len(products)


async def push_order_movements_to_grist(
    base_url: str,
    doc_id: str,
    api_key: str,
    db: AsyncSession,
    order_uuid: uuid.UUID,
    movement_type: str,
    market_order_id: str,
) -> None:
    """Push all movements of a given type for an order to Grist StockMovements."""
    result = await db.execute(
        select(StockMovement, RawMaterial)
        .join(RawMaterial, StockMovement.material_id == RawMaterial.id)
        .where(StockMovement.order_id == order_uuid, StockMovement.type == movement_type)
    )
    rows = result.all()
    for movement, material in rows:
        try:
            await push_stock_movement_to_grist(
                base_url, doc_id, api_key,
                material.name, movement_type, movement.quantity, market_order_id,
            )
        except Exception as exc:
            logger.warning("push_stock_movement_to_grist failed: %s", exc)


async def push_debug_after_stock_op(
    base_url: str,
    doc_id: str,
    api_key: str,
    db: AsyncSession,
    order_uuid: uuid.UUID,
    movement_type: str,
    market_order_id: str,
) -> None:
    """Push MaterialsStatus snapshot + order movements to Grist. Call after any stock operation."""
    try:
        await push_materials_status_to_grist(base_url, doc_id, api_key, db)
    except Exception as exc:
        logger.warning("push_materials_status_to_grist failed: %s", exc)
    await push_order_movements_to_grist(
        base_url, doc_id, api_key, db, order_uuid, movement_type, market_order_id,
    )
