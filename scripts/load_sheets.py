"""One-time script: import raw materials, products, and recipes from Google Sheets.

Usage:
  python -m scripts.load_sheets

Requires .env with GOOGLE_SPREADSHEET_ID and GOOGLE_SERVICE_ACCOUNT_FILE,
plus standard DB connection vars.
"""
import asyncio
import logging

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.agents.flower_stock.sheets_loader import load_from_sheets
import app.models  # noqa: F401 — registers all models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


async def main() -> None:
    if not settings.google_spreadsheet_id:
        raise SystemExit("GOOGLE_SPREADSHEET_ID is not set in .env")

    engine = create_async_engine(settings.database_url, echo=False)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        await load_from_sheets(
            db,
            settings.google_service_account_file,
            settings.google_spreadsheet_id,
        )
    await engine.dispose()
    print("Done.")


asyncio.run(main())
