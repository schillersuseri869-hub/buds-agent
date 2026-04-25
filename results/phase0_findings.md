# Phase 0: Yandex Market API Findings

Run date: 2026-04-25

## Auth
- Header: `Authorization: Bearer <token>`
- Token type: `y0__` OAuth 2.0 Bearer (obtained via oauth.yandex.ru, scope `market:partner-api`)
- ACMA token from Partner Cabinet does NOT work in Authorization header (colons cause RFC 6750 syntax error)

## 1. Financial stats (`POST /campaigns/{id}/stats/orders`)
- Status: 200 OK
- Top-level order fields: `id, creationDate, statusUpdateDate, status, partnerOrderId, paymentType, fake, deliveryRegion, items, payments, commissions, subsidies, buyerType, currency`
- Has `payments` field: YES (nested)
- Has `commissions` field: YES (nested)
- Has `subsidies` field: YES (nested) — likely contains buyer discount / Yandex subsidy data
- Has `buyerDiscount` at top level: NO (check inside `subsidies`)
- Has `boostCost` at top level: NO (check inside `commissions`)
- Decision: **API auto** — endpoint is available and returns economic data per order

## 2. Stock update (`PUT /campaigns/{id}/offers/stocks`)
- Status: 200 OK `{"status":"OK"}`
- Test SKU `TEST-NONEXISTENT-SKU` with `warehouseId=0` accepted without error
- warehouseId=0 works for test (real warehouseId to confirm with real SKU)
- Payload format confirmed: YES

## 3. Price update + quarantine
- Correct endpoint: `POST v2/businesses/{businessId}/offer-prices/updates` (not campaigns endpoint)
- Status: 200 OK `{"status":"OK"}`
- Shop uses "base prices" mode — prices must go via businessId endpoint, not campaignId
- Payload format: `{"offers": [{"offerId": "SKU", "price": {"value": N, "currencyId": "RUR", "discountBase": N}}]}`
- Quarantine endpoint: `POST v2/campaigns/{campaignId}/price-quarantine` — to check/confirm quarantined prices
- Auth note: Bearer token works for this endpoint

## 4. Schedule API
- `GET /campaigns/{id}/schedule` exists: NO (404 Not Found)
- `PUT /schedule` exists: likely NO
- Decision: **Schedule management not available via API** — must be done manually via Partner Cabinet

## 5. Order label
- Status: SKIP (no real ORDER_ID available at test time)
- Endpoint: `GET /campaigns/{id}/orders/{orderId}/delivery/labels`
- To test: run with `ORDER_ID=<real_id>` env var when first real order arrives

## 6. Webhooks / push settings
- `GET /campaigns/{id}/settings` returns shop settings (200 OK)
- Response contains delivery schedule but NO webhook/push configuration fields
- Push model configuration: **Partner Cabinet only** (not configurable via API)
- Webhook URL must be set in Partner Cabinet → Настройки → API
- Key fields visible in settings: `countryRegion, shopName, localRegion, delivery.schedule`

## Decisions
Based on findings:
- Economics data source: **API auto** (`/stats/orders`) — available, use for Order Agent
- Stock update: **API** — works, use `warehouseId=0` until confirmed otherwise
- Price update: **API** — use `v2/businesses/{businessId}/offer-prices/updates`
- Schedule management: **Manual** — API endpoint does not exist (404)
- Label source: **API download** — endpoint exists, test with real order
- Webhook config: **Partner Cabinet only** — set URL there, not via API

## Action items before next phase
1. Set webhook URL in Partner Cabinet → Настройки → push-уведомления → `http://82.22.3.55:8000/webhooks/market`
2. Test `/stats/orders` with real orders to confirm `subsidies`/`commissions` structure
3. Test order label download with real ORDER_ID
