import logging
from decimal import Decimal, InvalidOperation

from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_products import MarketProduct
from app.models.raw_materials import RawMaterial
from app.models.recipes import Recipe

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]


def _sheets_service(service_account_file: str):
    creds = service_account.Credentials.from_service_account_file(
        service_account_file, scopes=_SCOPES
    )
    return build("sheets", "v4", credentials=creds)


def _get_range(service, spreadsheet_id: str, range_: str) -> list[list]:
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=spreadsheet_id, range=range_)
        .execute()
    )
    return result.get("values", [])


def _d(value: str) -> Decimal:
    try:
        return Decimal(str(value).replace(",", ".").strip())
    except InvalidOperation:
        return Decimal("0")


async def load_materials(
    db: AsyncSession, rows: list[list]
) -> dict[str, RawMaterial]:
    """Upsert raw_materials from sheet rows. Returns {name: RawMaterial}."""
    loaded: dict[str, RawMaterial] = {}
    for row in rows:
        if len(row) < 5 or not row[0].strip():
            continue
        name, type_, unit = row[0].strip(), row[1].strip(), row[2].strip()
        initial_stock, cost = _d(row[3]), _d(row[4])

        result = await db.execute(
            select(RawMaterial).where(RawMaterial.name == name)
        )
        mat = result.scalar_one_or_none()
        if mat is None:
            mat = RawMaterial(
                name=name, type=type_, unit=unit,
                physical_stock=initial_stock, cost_per_unit=cost,
            )
            db.add(mat)
        else:
            mat.type = type_
            mat.unit = unit
            mat.cost_per_unit = cost
        await db.commit()
        await db.refresh(mat)
        loaded[name] = mat
        logger.info("Loaded material: %s", name)
    return loaded


async def load_products(
    db: AsyncSession, rows: list[list]
) -> dict[str, MarketProduct]:
    """Upsert market_products from sheet rows. Returns {market_sku: MarketProduct}."""
    loaded: dict[str, MarketProduct] = {}
    for row in rows:
        if len(row) < 6 or not row[0].strip():
            continue
        sku = row[0].strip()
        name = row[1].strip()
        catalog_price, crossed_price = _d(row[2]), _d(row[3])
        min_price, optimal_price = _d(row[4]), _d(row[5])

        result = await db.execute(
            select(MarketProduct).where(MarketProduct.market_sku == sku)
        )
        prod = result.scalar_one_or_none()
        if prod is None:
            prod = MarketProduct(
                market_sku=sku, name=name,
                catalog_price=catalog_price, crossed_price=crossed_price,
                min_price=min_price, optimal_price=optimal_price,
            )
            db.add(prod)
        else:
            prod.name = name
            prod.catalog_price = catalog_price
            prod.crossed_price = crossed_price
            prod.min_price = min_price
            prod.optimal_price = optimal_price
        await db.commit()
        await db.refresh(prod)
        loaded[sku] = prod
        logger.info("Loaded product: %s — %s", sku, name)
    return loaded


async def load_recipes(
    db: AsyncSession,
    rows: list[list],
    products: dict[str, MarketProduct],
    materials: dict[str, RawMaterial],
) -> int:
    """Upsert recipes. Returns count of recipes loaded."""
    count = 0
    for row in rows:
        if len(row) < 3 or not row[0].strip():
            continue
        sku, mat_name, qty = row[0].strip(), row[1].strip(), _d(row[2])

        prod = products.get(sku)
        mat = materials.get(mat_name)
        if prod is None:
            logger.warning("Recipe references unknown SKU: %s", sku)
            continue
        if mat is None:
            logger.warning("Recipe references unknown material: %s", mat_name)
            continue

        result = await db.execute(
            select(Recipe).where(
                Recipe.product_id == prod.id,
                Recipe.material_id == mat.id,
            )
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


async def load_from_sheets(
    db: AsyncSession,
    service_account_file: str,
    spreadsheet_id: str,
) -> None:
    """Load all data from Google Sheets into DB (upsert)."""
    service = _sheets_service(service_account_file)

    mat_rows = _get_range(service, spreadsheet_id, "Сырьё!A2:E")
    prod_rows = _get_range(service, spreadsheet_id, "Товары!A2:F")
    recipe_rows = _get_range(service, spreadsheet_id, "Рецепты!A2:C")

    materials = await load_materials(db, mat_rows)
    products = await load_products(db, prod_rows)
    await load_recipes(db, recipe_rows, products, materials)
    logger.info(
        "Sheets load complete: %d materials, %d products",
        len(materials), len(products),
    )
