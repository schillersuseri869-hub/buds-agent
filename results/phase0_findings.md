# Phase 0: Yandex Market API Findings

Run date: 2026-04-__

## 1. Financial stats (`POST /campaigns/{id}/stats/orders`)
- Has `buyerDiscount` per order: [YES / NO]
- Has `boostCost` per order: [YES / NO]
- Has `salesCommission` per order: [YES / NO]
- Decision: [auto via API / manual CSV upload with `/отчёт` command]

## 2. Stock update (`PUT /campaigns/{id}/offers/stocks`)
- Required `warehouseId`: [value]
- Payload format confirmed: [YES / NO]
- Notes:

## 3. Price update + quarantine
- Quarantine triggered when: [condition, e.g. price drops >X%]
- Quarantine signaled by field: [fieldName in response]
- Notes:

## 4. Schedule API
- `GET /schedule` exists: [YES / NO]
- `PUT /schedule` exists: [YES / NO]
- Delay for changes to apply: [~X hours]
- Notes:

## 5. Order label
- Format: [PDF binary / redirect URL / other]
- Endpoint confirmed working: [YES / NO]
- Notes:

## 6. Webhooks
- Model: [push (Yandex sends to our URL) / pull]
- Configuration: [API / Partner Cabinet only]
- Key fields in push payload for ORDER_STATUS_CHANGED:
  ```json
  {}
  ```

## Decisions
Based on findings:
- Economics data source: [API auto / manual CSV]
- Schedule management: [API / manual]
- Label source: [API download / Partner Cabinet]
