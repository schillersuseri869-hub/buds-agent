"""One-time script: create WriteOffs table in Grist.

Usage:
  python -m scripts.create_grist_tables
"""
import asyncio
import httpx
from app.config import settings


async def main() -> None:
    base = settings.grist_url
    doc = settings.grist_doc_id
    key = settings.grist_api_key
    headers = {"Authorization": f"Bearer {key}"}

    async with httpx.AsyncClient() as client:
        # Check if table already exists
        r = await client.get(f"{base}/api/docs/{doc}/tables", headers=headers)
        r.raise_for_status()
        existing = [t["id"] for t in r.json().get("tables", [])]

        if "WriteOffs" in existing:
            print("Table WriteOffs already exists, skipping creation.")
        else:
            payload = {
                "tables": [{
                    "id": "WriteOffs",
                    "columns": [
                        {"id": "date",         "fields": {"label": "date",         "type": "DateTime:Europe/Moscow"}},
                        {"id": "material",     "fields": {"label": "material",     "type": "Text"}},
                        {"id": "type",         "fields": {"label": "type",         "type": "Text"}},
                        {"id": "quantity",     "fields": {"label": "quantity",     "type": "Numeric"}},
                        {"id": "unit",         "fields": {"label": "unit",         "type": "Text"}},
                        {"id": "cost_per_unit","fields": {"label": "cost_per_unit","type": "Numeric"}},
                        {"id": "total_cost",   "fields": {"label": "total_cost",   "type": "Numeric"}},
                    ],
                }]
            }
            r = await client.post(
                f"{base}/api/docs/{doc}/tables",
                headers=headers,
                json=payload,
                timeout=15.0,
            )
            r.raise_for_status()
            print("Table WriteOffs created.")


asyncio.run(main())
