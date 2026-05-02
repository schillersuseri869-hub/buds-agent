# Add Stock Command Design

## Goal

Allow owner and florist to record stock arrivals and write-offs via Telegram — either manually through guided FSM flows (`/add`, `/write_off`), or automatically by sending a supplier invoice photo or `.xlsx` file.

## Architecture

Three entry points converging on stock operations:

```
/add → FSM (SelectMaterial → EnterQuantity → EnterPrice) → record_arrival
photo/screenshot → Claude Haiku vision → item list ┐
.xlsx file → openpyxl parser → item list           ┘→ synonym lookup → per-item confirm → record_arrival

/write_off → FSM (SelectType → SelectMaterial → EnterQuantity → [SelectOrder]) → record_write_off
```

A new `supplier_aliases` table maps supplier-specific names (e.g. "АЛТАЙ", "БАЛТИКА") to internal material IDs. On first encounter the AI proposes a match, the user confirms, and the mapping is saved. Subsequent invoices with the same name resolve instantly without AI.

## Components

### 1. FSM: Manual `/add` — `app/bot/add_stock_fsm.py`

States:
- `SelectMaterial` — bot shows inline keyboard of all materials from DB; user taps one
- `EnterQuantity` — bot asks "Сколько?"; user types a number
- `EnterPrice` — bot asks "Цена за единицу (₽)?"; user types a number

On completion: calls `stock_ops.record_arrival`, bot replies with confirmation:
```
✅ Приход: 50 шт. «Роза 40см» по 80₽
Остаток на складе: 350 шт.
```

`/cancel` at any state resets FSM and replies "Отменено."

Registered on both `owner_router` and `florist_router`.

### 2. Invoice handler — `app/bot/scan_invoice.py`

Handles two Telegram message types:
- `Message.photo` — downloads highest-res photo
- `Message.document` with `.xlsx` mime type — downloads file

After extracting raw bytes, delegates to `invoice_reader`. Then runs the per-item confirmation loop (see below).

### 3. Invoice reader — `app/agents/flower_stock/invoice_reader.py`

Two functions:

**`read_photo(image_bytes: bytes, material_names: list[str]) -> list[InvoiceItem]`**
Sends image to Claude Haiku (`claude-haiku-4-5-20251001`) with a prompt that includes the list of known material names and asks it to extract `[{name, qty, price}]` as JSON. Returns parsed list.

**`read_xlsx(file_bytes: bytes) -> list[InvoiceItem]`**
Opens with `openpyxl`, scans all rows looking for columns that look like (name, qty, price) — tries header-based detection first, falls back to column position heuristic. Returns list of `InvoiceItem(name, qty, price)`.

`InvoiceItem` is a simple dataclass: `name: str, qty: Decimal, price: Decimal`.

### 4. Synonym ops — `app/agents/flower_stock/synonym_ops.py`

**`lookup_alias(db, alias_text: str) -> RawMaterial | None`**
Searches `supplier_aliases` by lowercased alias_text. Returns matched material or None.

**`save_alias(db, alias_text: str, material_id: uuid.UUID) -> None`**
Inserts row into `supplier_aliases`. Ignores conflict (upsert).

### 5. Per-item confirmation loop — inside `scan_invoice.py`

For each `InvoiceItem`:
1. Look up alias → if found, skip to confirmation with matched material
2. If not found → call AI to propose best match from material list → show proposal
3. Show message:
   ```
   📦 АЛТАЙ × 50 шт. по 85₽
   → Похоже на «Хризантема белая»?

   [✅ Да]  [⏭ Пропустить]
   ```
4. User can also **type a material name** instead of pressing a button — bot looks it up in DB
5. On "✅ Да" or text confirmation: save alias + call `record_arrival`
6. On "⏭ Пропустить": skip item, continue to next
7. After all items: summary "Записано N из M позиций."

State between items is stored in FSMContext (item queue + current index).

### 6. FSM: Write-off `/write_off` — `app/bot/write_off_fsm.py`

States:
- `SelectType` — bot shows: `[Брак]` `[Порча]` `[К заказу]`
- `SelectMaterial` — inline keyboard of all materials
- `EnterQuantity` — bot asks "Сколько?"; user types a number
- `SelectOrder` *(only for "К заказу")* — bot shows last 20 orders as inline keyboard, format: `"MKT-123 · Роза + Хризантема"`

On completion:
- Брак → `stock_ops.record_spoilage(db, material_id, qty, movement_type="defect")`
- Порча → `stock_ops.record_spoilage(db, material_id, qty, movement_type="spoilage")`
- К заказу → `stock_ops.record_extra_debit(db, material_id, order_id, qty)`

Confirmation message:
```
✅ Списано: 3 шт. «Хризантема белая» (брак)
Остаток: 47 шт.
```
or for order-linked:
```
✅ Доп. списание: 2 шт. «Хризантема белая» к заказу MKT-123
```

`/cancel` resets FSM at any state. Registered on both `owner_router` and `florist_router`.

`stock_ops.get_recent_orders(db, limit=20) -> list[Order]` — new helper, orders by `created_at DESC`.

## Data Model

New table `supplier_aliases`:
```sql
CREATE TABLE supplier_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    alias_text VARCHAR NOT NULL UNIQUE,  -- lowercase, stripped
    material_id UUID NOT NULL REFERENCES raw_materials(id) ON DELETE CASCADE
);
CREATE INDEX ON supplier_aliases (alias_text);
```

New Alembic migration.

## AI Usage

- Model: `claude-haiku-4-5-20251001`
- Used for: (a) vision extraction from photos/screenshots, (b) proposing alias match when alias not in DB
- Not used for: xlsx parsing, confirmed aliases (resolved from DB)
- Prompt for vision extraction returns structured JSON; parsed with `json.loads`
- Prompt for alias matching: "Given these materials: [...]. Which best matches '{alias}'? Reply with just the material name or 'unknown'."

### 7. Inventory audit `/inventory` — `app/bot/inventory_fsm.py`

FSM goes through each material one by one:
- `AuditMaterial` — shows "Роза 40см: в системе 47 шт. Сколько по факту?" → user types number
- On each answer: calls `stock_ops.record_inventory_correction(db, material_id, actual_qty)` which sets `physical_stock = actual_qty` and logs a `StockMovement(type="inventory_correction", quantity=delta)`
- Skips materials with active reservations (warns: "У «Роза» есть резерв, пропускаю")
- At end: summary "Инвентаризация завершена. Исправлено N позиций." with deltas

`/cancel` stops audit mid-way; already-confirmed materials are saved. Registered on `owner_router` only (not florist).

### 8. Movement history `/history` — inline query in `owner_bot.py` and `florist_bot.py`

No FSM. Flow:
1. `/history` → inline keyboard of all materials
2. User taps material → bot shows last 20 `StockMovement` rows for that material, newest first
3. Format per row: `"01.05 14:30 · Приход +50 шт. по 80₽"` / `"01.05 09:15 · Списание -3 шт. (порча)"`

`stock_ops.get_material_history(db, material_id, limit=20) -> list[StockMovement]` — new helper.

### 9. Cost report `/report` — inline query in `owner_bot.py` only

No FSM. Flow:
1. `/report` → inline keyboard: `[За сегодня]` `[За неделю]` `[За месяц]`
2. Bot replies with:
   ```
   📊 Отчёт за неделю (25.04–01.05)
   
   Закупки: 12 500₽ (8 позиций)
   Списания: 1 200₽
   Стоимость склада: 34 800₽
   ```

`stock_ops.get_report(db, since: datetime) -> ReportData` — new helper that aggregates `StockMovement` rows. `ReportData` is a dataclass with `arrivals_cost`, `write_offs_cost`, `current_stock_value`.

## Error Handling

- If vision extraction fails → bot replies "Не удалось распознать фото. Попробуй ещё раз или используй /add."
- If xlsx has no recognizable columns → same fallback message
- If AI returns 'unknown' for alias match → skip item, notify "Не удалось сопоставить: АЛТАЙ"
- All errors logged, owner gets alert via `_alert()`

## Testing

- Unit tests for `invoice_reader`: mock Anthropic client, test JSON parsing; test xlsx parsing with in-memory workbook
- Unit tests for `synonym_ops`: lookup hit/miss, save+lookup roundtrip
- Unit tests for `/add` FSM: mock DB, step through states, verify `record_arrival` called with correct args
- Unit tests for `/write_off` FSM: all three types; verify correct `stock_ops` method called per type; verify "К заказу" triggers order list step
- Unit tests for `/inventory` FSM: correction recorded correctly, delta calculated, reserved materials skipped
- Unit tests for `get_material_history` and `get_report` helpers: correct filtering by date and material
- Unit tests for confirmation loop: alias found → direct confirm; alias missing → AI propose → confirm → save
