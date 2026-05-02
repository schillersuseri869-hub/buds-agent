# Eucalyptus Stock Management — Design Spec

**Date:** 2026-05-02  
**Status:** Approved

## Problem

Eucalyptus is consumed at 200g per bouquet. When stock runs out, the system must automatically hide all `-e` SKUs from the Yandex Market storefront and prompt the florist/owner via Telegram to report how much eucalyptus is actually in the fridge (it may differ from the computed remainder).

## Trigger

Event: `order.created` — after `reserve_materials()` runs, check whether:
1. The order contains any SKU with `-e` in it
2. Eucalyptus net available (`physical_stock - reserved`) is below 200g

Both conditions must be true to send an alert.

## Storefront Hiding

Hiding `-e` SKUs from the Market storefront happens **automatically** via the existing `_update_storefront()` call in `handle_order_created`. The function `compute_available_stocks()` returns 0 for `-e` products when eucalyptus is insufficient. No additional explicit hide call is needed.

## Data Flow

```
order.created
  └─ handle_order_created()
       ├─ reserve_materials()
       ├─ _update_storefront()          ← hides -e SKUs if eucalyptus low
       └─ [NEW] eucalyptus check
            └─ if has -e SKUs AND is_eucalyptus_low()
                 └─ _alert_all("⚠️ Эвкалипт заканчивается...", keyboard)

florist or owner taps button
  └─ handle_eucalyptus_callback(qty_g)
       ├─ if qty_g == 0: return (keep -e off market)
       ├─ set_eucalyptus_stock(db, qty_g)
       ├─ _update_storefront()          ← restores -e SKUs
       └─ _alert_all("✅ Эвкалипт: {qty}г. Позиции возвращены.")
```

## Telegram UX

Alert message (sent to both owner and florist):
> ⚠️ Эвкалипт заканчивается. Сколько осталось в холодильнике?

Inline keyboard:
```
[200г]  [400г]  [600г]
[Не добавлять]
```

On tap: button text becomes `✅ {label}`, confirmation sent to both chats.

## Files Changed

### `app/agents/flower_stock/stock_ops.py`

Add two functions:

**`is_eucalyptus_low(db) → bool`**  
Returns True if `physical_stock - reserved < 200g`. Uses material name `"evkalipt"`. Returns False if material not found.

**`set_eucalyptus_stock(db, quantity) → RawMaterial`**  
Sets `physical_stock` to an absolute value (florist's physical count). Logs a `StockMovement` with `type="arrival"` and `quantity=quantity` (the reported total, not a delta — this is a manual correction, not a delivery). Uses `with_for_update()` for safety. Idempotent on double-tap.

### `app/agents/flower_stock/agent.py`

- Constructor: add `florist_bot: Bot | None = None` parameter
- Add module-level `_EVKALIPT_KEYBOARD` inline keyboard constant
- Add `_alert_all(text, markup=None)`: sends to `owner_telegram_id` via `owner_bot`, and to `florist_telegram_id` via `florist_bot` if both are set
- Add `handle_eucalyptus_callback(qty_g: int)`: handles restock response
- In `handle_order_created`, after `reserve_materials`: add eucalyptus check and alert

### `app/bot/owner_bot.py`

Add `register_eucalyptus_callbacks(flower_stock_agent)`:  
Registers a callback handler for `evk_restock:*` on `owner_router`. Calls `flower_stock_agent.handle_eucalyptus_callback(qty_g)`.

### `app/bot/florist_bot.py`

Same as `owner_bot.py` but using `florist_router`.

### `app/main.py`

- Pass `florist_bot=florist_bot` when constructing `FlowerStockAgent`
- Call `register_eucalyptus_callbacks(flower_stock_agent)` for both bots (florist_bot guarded by `if florist_bot`)

## Error Handling

- `is_eucalyptus_low`: returns False (safe default) if material not in DB
- `_alert_all`: if florist_bot is None or florist_telegram_id is None, skip florist message silently
- `handle_eucalyptus_callback`: if `qty_g == 0`, return immediately without updating stock

## Constraints

- No new DB migrations: `StockMovement.type="arrival"` already exists
- `florist_telegram_id` already in `Settings` (optional, `int | None`)
- No timeout/reminder logic in this version: if florist ignores the message, `-e` SKUs stay hidden until the next order triggers a restock prompt

## Testing Notes

- Unit test `is_eucalyptus_low` with: no material, sufficient stock, exactly 200g, below 200g
- Unit test `set_eucalyptus_stock` verifies `physical_stock` and `StockMovement` row
- Integration: send a mock `order.created` with `-e` SKU when eucalyptus < 200g, verify Telegram message is sent
