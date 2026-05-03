"""One-time script: create InvoiceMappings table in Grist.

Usage:
  python -m scripts.create_invoice_mappings_table
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
        r = await client.get(f"{base}/api/docs/{doc}/tables", headers=headers)
        r.raise_for_status()
        existing = [t["id"] for t in r.json().get("tables", [])]

        if "InvoiceMappings" in existing:
            print("Table InvoiceMappings already exists, skipping creation.")
            return

        payload = {
            "tables": [{
                "id": "InvoiceMappings",
                "columns": [
                    {"id": "invoice_name",  "fields": {"label": "invoice_name",  "type": "Text"}},
                    {"id": "material_code", "fields": {"label": "material_code", "type": "Text"}},
                    {"id": "unit",          "fields": {"label": "unit",          "type": "Text"}},
                    {"id": "notes",         "fields": {"label": "notes",         "type": "Text"}},
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
        print("Table InvoiceMappings created.")


asyncio.run(main())
