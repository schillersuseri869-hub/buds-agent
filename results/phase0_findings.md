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
- Status: 423 LOCKED
- Error: `{"code":"LOCKED","message":"Partner use only default price"}`
- **CRITICAL**: Shop is currently set to use Yandex default pricing — custom price updates via API are blocked
- Action required: Switch shop to custom pricing mode in Partner Cabinet before Pricing Agent can work
- Quarantine field: not yet testable until pricing mode is switched

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
- Price update: **BLOCKED** — switch shop to custom pricing mode in Partner Cabinet first
- Schedule management: **Manual** — API endpoint does not exist (404)
- Label source: **API download** — endpoint exists, test with real order
- Webhook config: **Partner Cabinet only** — set URL there, not via API

## Action items before next phase
1. In Partner Cabinet: switch pricing mode from "default" to "custom" to unblock price API
2. Set webhook URL in Partner Cabinet → Настройки → push-уведомления → URL нашего VPS
3. Test `/stats/orders` response with real orders to confirm `subsidies`/`commissions` structure
4. Test order label download with real ORDER_ID
