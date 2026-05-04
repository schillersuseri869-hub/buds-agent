# Pricing Report — Технический дизайн

**Дата:** 2026-05-04
**Версия:** 1.0
**Scope:** Таблица ценообразования в Grist с цветовой индикацией

---

## Контекст

Владельцу нужна сводная таблица по ценам в реальном времени: каталожная цена, витринная, участие в акциях, promoPrice и цветовое выделение аномалий. Таблица живёт в Grist (подключён к PostgreSQL напрямую), критические алерты дублируются в Telegram.

---

## Структура таблицы

**Одна строка = один SKU × одна акция.** Если SKU участвует в 3 акциях — 3 строки. Если не участвует ни в одной — 1 строка с пустыми полями акции.

### Колонки

| Колонка | Источник | Описание |
|---|---|---|
| Название | `market_products.name` | Название товара |
| SKU | `market_products.market_sku` | Артикул |
| Каталожная цена | `market_products.catalog_price` | Цена в каталоге Яндекса |
| Мин. для акций | `market_products.optimal_price × 1.10` | Нижний порог promoPrice |
| Витринная цена | `market_products.storefront_price` | Цена которую видит покупатель |
| promoPrice | `promo_participations.promo_price` | Наша цена в акции |
| Скидка % | вычисляется | `(1 - promo_price / catalog_price) × 100` |
| Акция | `promos.name` | Название акции Яндекса |
| Тип акции | `promos.type` | fixed_discount / direct_discount |
| Действует до | `promos.ends_at` | Дата окончания |
| Статус | вычисляется | Цветовой статус строки |

---

## Изменения в БД (одна миграция)

### Новая таблица `promos`

```python
class Promo(Base):
    __tablename__ = "promos"

    promo_id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

### Новое поле в `market_products`

```python
storefront_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
```

---

## Изменения в Pricing Agent

### Фаза 1 — сохранение метаданных акций

При вызове `get_promos()` — сохранять/обновлять `promos`:

```python
async def _sync_promos(self, available_promos: list[dict]) -> None:
    async with self._db_factory() as db:
        for promo in available_promos:
            db.merge(Promo(
                promo_id=promo.get("id") or promo.get("promoId"),
                name=promo.get("name", ""),
                type=promo.get("mechanicsType", ""),
                starts_at=parse_dt(promo.get("startDate")),
                ends_at=parse_dt(promo.get("endDate")),
                updated_at=datetime.now(timezone.utc),
            ))
        await db.commit()
```

### Фаза 5 — обновление storefront_price

После разбора отчёта — обновлять `market_products.storefront_price`:

```python
async def _update_storefront_prices(
    self, products: list[MarketProduct], report: PricesReport
) -> None:
    async with self._db_factory() as db:
        for prod in products:
            price = report.storefront.get(prod.market_sku)
            if price is not None:
                prod.storefront_price = price
                db.add(prod)
        await db.commit()
```

---

## PostgreSQL View для Grist

```sql
CREATE OR REPLACE VIEW v_pricing_report AS
SELECT
    mp.name,
    mp.market_sku,
    mp.catalog_price,
    ROUND(mp.optimal_price * 1.10, 0)  AS min_promo_price,
    mp.storefront_price,
    mp.optimal_price,
    pp.promo_price,
    pp.promo_type,
    CASE
        WHEN pp.promo_price IS NOT NULL AND mp.catalog_price > 0
        THEN ROUND((1 - pp.promo_price / mp.catalog_price) * 100)
        ELSE NULL
    END AS discount_pct,
    pr.name       AS promo_name,
    pr.type       AS promo_type_name,
    pr.starts_at,
    pr.ends_at,
    CASE
        WHEN pp.promo_price IS NULL
            THEN 'no_promo'
        WHEN pp.promo_price < mp.optimal_price * 1.10
            THEN 'danger'
        WHEN mp.storefront_price > mp.optimal_price * 1.05
            THEN 'warning'
        WHEN mp.storefront_price < mp.optimal_price
            THEN 'info'
        ELSE 'ok'
    END AS status
FROM market_products mp
LEFT JOIN promo_participations pp ON pp.product_id = mp.id
LEFT JOIN promos pr ON pr.promo_id = pp.promo_id
ORDER BY
    CASE WHEN pp.promo_price < mp.optimal_price * 1.10 THEN 0
         WHEN mp.storefront_price > mp.optimal_price * 1.05 THEN 1
         WHEN mp.storefront_price < mp.optimal_price THEN 2
         WHEN pp.promo_price IS NOT NULL THEN 3
         ELSE 4 END,
    mp.name;
```

---

## Цветовые правила в Grist

Grist поддерживает условное форматирование через формулы на колонке. Правило применяется к строке целиком через цвет фона колонки `status`:

| Значение `status` | Цвет фона | Цвет текста | Смысл |
|---|---|---|---|
| `danger` | `#2d1a1a` | `#ef4444` 🔴 | promoPrice ниже порога — требует действий |
| `warning` | `#2d200a` | `#f97316` 🟠 | Витрина завышена — агент снизит promoPrice |
| `info` | `#0d1f2d` | `#3b82f6` 🔵 | Витрина ниже optimal — Яндекс платит разницу |
| `ok` | `#0d1f12` | `#22c55e` 🟢 | Всё в норме |
| `no_promo` | `#18181b` | `#71717a` ⚫ | Не участвует в акциях |

---

## Telegram-алерты

Уже реализованы в Pricing Agent:
- 🔴 `danger` — немедленный алерт при обнаружении
- 🟠 `warning` — в итоговом отчёте цикла

🔵 и 🟢 — только в Grist, в Telegram не дублируются.

---

## Порядок реализации

1. Alembic-миграция: таблица `promos` + поле `storefront_price` в `market_products`
2. Модель `Promo` в `app/models/`
3. Pricing Agent: `_sync_promos()` в фазе 1, `_update_storefront_prices()` в фазе 5
4. PostgreSQL view `v_pricing_report`
5. Настройка таблицы в Grist (подключение к view, цветовые правила)

---

## Не входит в MVP

- Фильтрация по статусу в Grist (настраивается пользователем вручную)
- Исторические данные (есть в `price_history`)
- Автоматические уведомления о завершении акций
