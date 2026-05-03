"""One-time script: add 26 Alst60-mix SKUs to Grist Products + Recipes.

Grist formula columns (min_price, optimal_price, catalog_price, crossed_price)
are calculated automatically — this script only writes input fields.

Usage:
  python -m scripts.add_alst60_mix_skus
"""
import asyncio

import httpx

from app.config import settings

# (qty, is_pr)
SKUS = [
    (7,  True),  (9,  False), (11, False), (13, True),
    (15, False), (17, False), (18, True),  (19, False),
    (21, False), (22, True),  (23, False), (25, False),
    (27, False), (29, False), (31, False), (32, True),
    (33, False), (35, False), (37, False), (38, False),
    (39, False), (41, False), (42, True),  (45, False),
    (49, False), (55, True),
]


def _sku(qty: int, is_pr: bool) -> str:
    return f"Alst60-mix-{qty}-h60" + ("-pr" if is_pr else "")


def _name(qty: int, is_pr: bool) -> str:
    base = f"Альстромерия микс {qty}шт 60см"
    return base + " PR" if is_pr else base


def _con_kit(qty: int) -> str:
    return "con-kit-s" if qty <= 13 else "con-kit-l"


async def _fetch_table(client: httpx.AsyncClient, table: str) -> list[dict]:
    r = await client.get(
        f"{settings.grist_url}/api/docs/{settings.grist_doc_id}/tables/{table}/records",
        headers={"Authorization": f"Bearer {settings.grist_api_key}"},
        timeout=30.0,
    )
    r.raise_for_status()
    return [{**rec["fields"], "_id": rec["id"]} for rec in r.json().get("records", [])]


async def _add_records(client: httpx.AsyncClient, table: str, records: list[dict]) -> None:
    r = await client.post(
        f"{settings.grist_url}/api/docs/{settings.grist_doc_id}/tables/{table}/records",
        headers={"Authorization": f"Bearer {settings.grist_api_key}"},
        json={"records": [{"fields": rec} for rec in records]},
        timeout=30.0,
    )
    r.raise_for_status()


async def main() -> None:
    async with httpx.AsyncClient() as client:
        prod_rows = await _fetch_table(client, "Products")
        existing_skus = {str(r.get("market_sku", "")).strip() for r in prod_rows}

        recipe_rows = await _fetch_table(client, "Recipes")
        existing_recipes = {
            (str(r.get("market_sku", "")).strip(), str(r.get("material_name", "")).strip())
            for r in recipe_rows
        }

        new_products: list[dict] = []
        new_recipes: list[dict] = []

        for qty, is_pr in SKUS:
            sku = _sku(qty, is_pr)

            if sku not in existing_skus:
                new_products.append({
                    "market_sku": sku,
                    "name": _name(qty, is_pr),
                    "is_pr": is_pr,
                    "markup": 0,
                    "con_kit": _con_kit(qty),
                    "category": "Альстромерия",
                })
                print(f"  + product: {sku}")
            else:
                print(f"  SKIP (exists): {sku}")

            for mat_name, mat_qty in [("Alst60-mix", qty), ("box-l2", 1)]:
                if (sku, mat_name) not in existing_recipes:
                    new_recipes.append({
                        "market_sku": sku,
                        "material_name": mat_name,
                        "quantity": mat_qty,
                    })

        print()
        if new_products:
            print(f"Adding {len(new_products)} products...")
            await _add_records(client, "Products", new_products)
            print("Done.")

        if new_recipes:
            print(f"Adding {len(new_recipes)} recipe rows...")
            await _add_records(client, "Recipes", new_recipes)
            print("Done.")

        if not new_products and not new_recipes:
            print("Nothing to add — all SKUs already exist.")


asyncio.run(main())
