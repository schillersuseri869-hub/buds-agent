#!/usr/bin/env python3
"""
Phase 0: Yandex Market API capability discovery.

Run with real credentials on VPS:
    MARKET_API_TOKEN=... MARKET_CAMPAIGN_ID=... python scripts/test_market_api.py

Optional: ORDER_ID=12345 ... for label check.
"""
import asyncio
import json
import os
import httpx

BASE_URL = "https://api.partner.market.yandex.ru"
TOKEN = os.environ["MARKET_API_TOKEN"]
CAMPAIGN_ID = os.environ["MARKET_CAMPAIGN_ID"]
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


async def check_order_stats(client: httpx.AsyncClient):
    print("\n=== 1. Financial stats (POST /campaigns/{id}/stats/orders) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/stats/orders"
    r = await client.post(url, json={"dateFrom": "2026-04-01", "dateTo": "2026-04-22"}, headers=HEADERS)
    print(f"Status: {r.status_code}")
    if r.status_code == 200:
        orders = r.json().get("result", {}).get("orders", [])
        if orders:
            print("Sample order fields:", json.dumps(list(orders[0].keys()), ensure_ascii=False, indent=2))
        else:
            print("No orders in date range — try adjusting dates")
    else:
        print(f"Error: {r.text[:500]}")
    print("CHECK: Are buyerDiscount / boostCost / payments fields present per order?")


async def check_stock_update(client: httpx.AsyncClient):
    print("\n=== 2. Stock update (PUT /campaigns/{id}/offers/stocks) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/offers/stocks"
    payload = {
        "skus": [
            {"sku": "TEST-NONEXISTENT-SKU", "warehouseId": 0, "items": [{"type": "FIT", "count": 0}]}
        ]
    }
    r = await client.put(url, json=payload, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Note the required warehouseId value for your campaign")


async def check_price_update(client: httpx.AsyncClient):
    print("\n=== 3. Price update + quarantine (POST /campaigns/{id}/offer-prices/updates) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/offer-prices/updates"
    payload = {
        "offers": [{"id": "TEST-NONEXISTENT-SKU", "price": {"value": 1, "currencyId": "RUR"}}]
    }
    r = await client.post(url, json=payload, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Which field signals quarantine state? What triggers it?")


async def check_schedule_api(client: httpx.AsyncClient):
    print("\n=== 4. Shop schedule (GET /campaigns/{id}/schedule) ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/schedule"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:500]}")
    print("CHECK: Does GET exist? Does PUT /schedule exist? What is the apply delay?")


async def check_order_label(client: httpx.AsyncClient, order_id: str):
    print("\n=== 5. Order label download ===")
    if not order_id:
        print("SKIP: set ORDER_ID env var to test this check")
        return
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/orders/{order_id}/delivery/labels"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('content-type')}")
    if r.status_code == 200:
        with open("/tmp/test_label.pdf", "wb") as f:
            f.write(r.content)
        print("Label saved to /tmp/test_label.pdf")
    else:
        print(f"Error: {r.text[:500]}")


async def check_webhook_settings(client: httpx.AsyncClient):
    print("\n=== 6. Webhook / push notification settings ===")
    url = f"{BASE_URL}/campaigns/{CAMPAIGN_ID}/settings"
    r = await client.get(url, headers=HEADERS)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:1000]}")
    print("CHECK: Is push model configured via API or only via Partner Cabinet?")
    print("CHECK: What fields does a push notification contain for order status changes?")


async def main():
    order_id = os.environ.get("ORDER_ID", "")
    async with httpx.AsyncClient(timeout=30) as client:
        await check_order_stats(client)
        await check_stock_update(client)
        await check_price_update(client)
        await check_schedule_api(client)
        await check_order_label(client, order_id)
        await check_webhook_settings(client)
    print("\n=== Done — fill in results/phase0_findings.md ===")


if __name__ == "__main__":
    asyncio.run(main())
