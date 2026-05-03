"""One-time script: import raw materials, products, and recipes from Grist.

Usage:
  python -m scripts.load_sheets

Requires .env with GRIST_URL, GRIST_DOC_ID, GRIST_API_KEY,
plus standard DB connection vars.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.agents.flower_stock.sheets_loader import load_from_grist
import app.models  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    if not settings.grist_doc_id:
        raise SystemExit("GRIST_DOC_ID is not set in .env")
    if not settings.grist_api_key:
        raise SystemExit("GRIST_API_KEY is not set in .env")

    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        n_mat, n_prod = await load_from_grist(
            db,
            settings.grist_url,
            settings.grist_doc_id,
            settings.grist_api_key,
        )
    await engine.dispose()
    print(f"Done: {n_mat} materials, {n_prod} products.")


asyncio.run(main())
