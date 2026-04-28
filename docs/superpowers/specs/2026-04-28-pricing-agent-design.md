# Pricing Agent — Технический дизайн

**Дата:** 2026-04-28
**Версия:** 1.0
**Scope:** MVP Pricing Agent — мониторинг цен, синхронизация каталога, управление акциями

---

## Контекст

Pricing Agent — четвёртый агент системы BUDS. Работает по расписанию каждые 3 часа. Задача: синхронизировать каталожные цены из БД с Яндекс Маркетом, мониторить витринные цены, управлять участием SKU в акциях.

**Почему именно 3 часа:** баланс между актуальностью данных и нагрузкой на API (отчёт goods-prices/generate — тяжёлая операция).

---

## Модель ценообразования

### Типы SKU

**Обычные SKU:**
```
optimal      = себестоимость × 5
value        = optimal × 1.50   ← каталожная цена (витрина без акций)
discountBase = value × 1.40     ← зачёркнутая цена
minimumForBestseller = optimal  ← Яндекс не уходит ниже за свой счёт
promoPrice floor = optimal × 1.10
```

**Спец. SKU (множитель ×9):**
```
optimal      = себестоимость × 9
Остальные формулы идентичны обычным SKU
```

**`pr`-SKU (помечены флагом `is_pr = true`):**
```
optimal      = себестоимость × 5 (или × 9)
value        = optimal × 1.90   ← выше чем у обычных
discountBase = value × 1.40
Стратегия:   участвуем ВО ВСЕХ акциях, но по максимальной разрешённой цене
             → никогда не снижаем promoPrice самостоятельно
Логика:      показы во всех акциях + максимальная маржа при редких продажах
Буст:        вручную (автоматизация — будущая задача)
```

### Участие в акциях

| Тип акции | Обычные SKU | pr-SKU |
|---|---|---|
| Фиксированная скидка (10/15/20%) | Добавляем все автоматически | Добавляем все, цену не трогаем |
| Изменяемая скидка (DIRECT_DISCOUNT) | promoPrice = optimal × 1.10 | promoPrice = максимум разрешённый Яндексом¹ |
| Яндекс не разрешает нашу цену (обычный) | Алерт владельцу, не добавляем | — |

¹ **"Максимум Яндекса" для pr-SKU:** пробуем добавить по `value`. Яндекс возвращает допустимый диапазон (`maxPromoPrice`) в ответе `promos/offers` или в ошибке отклонения. Устанавливаем promoPrice = этот максимум. Если API не возвращает максимум явно — уточняется в ходе Phase 0 тестирования этого эндпоинта.

**Мониторинг витрины (только обычные SKU):**
```
если витрина > optimal × 1.05:
    снижаем promoPrice на шаг ≈ optimal × 0.10–0.15 (итерационно)
    пол: promoPrice ≥ optimal × 1.10 — никогда не нарушается

если витрина < optimal:
    информационный алерт (Яндекс покрывает разницу сам)
    мы получаем promoPrice ≥ optimal × 1.10

если promoPrice < optimal × 1.10:
    алерт владельцу, не меняем автоматически
```

**Цель мониторинга:** быть в рынке при 7–10 заказах в день, не максимизировать объём.
**Шаг:** консервативный, итерационный. Яндекс реагирует приблизительно предсказуемо — сверяем результат в следующем цикле.

---

## Архитектура

### Структура файлов

```
app/agents/pricing_agent/
├── __init__.py
├── agent.py          ← PricingAgent, APScheduler job (единый цикл)
├── market_api.py     ← все вызовы Яндекс Маркет API
└── price_engine.py   ← бизнес-логика: расчёты, сравнения, решения
```

### Подключение в `main.py`

```python
from app.agents.pricing_agent.agent import PricingAgent

pricing_agent = PricingAgent(AsyncSessionLocal, owner_bot, settings, scheduler)
pricing_agent.schedule()  # регистрирует job в APScheduler
```

---

## Единый цикл (каждые 3 часа)

### Полный поток

```
APScheduler: каждые 3 часа, max_instances=1, coalesce=True

ФАЗА 1 — СБОР ДАННЫХ (параллельно где возможно)
  ├── Читаем market_products из DB (optimal_price, catalog_price,
  │   crossed_price, is_pr, market_sku)
  ├── POST v2/reports/goods-prices/generate
  │   → polling каждые 30 сек, таймаут 5 мин
  │   → парсим витринные цены по SKU
  ├── POST v2/businesses/{id}/promos
  │   → список доступных акций (тип, скидка, даты)
  └── POST v2/businesses/{id}/promos/offers
      → какие SKU где участвуют и по какой цене

ФАЗА 2 — СИНХРОНИЗАЦИЯ КАТАЛОГА
  ├── Для каждого SKU: сравниваем DB vs Market
  │   (catalog_price, crossed_price, minimumForBestseller)
  └── Расхождение → обновляем автоматически
      POST v2/businesses/{id}/offer-prices/updates (батч)

ФАЗА 3 — МОНИТОРИНГ ВИТРИНЫ
  ├── Обычные SKU в акции:
  │   ├── витрина > optimal × 1.05
  │   │   → new_promo = max(promo - шаг, optimal × 1.10)
  │   │   → обновляем через promos/offers/update
  │   ├── витрина < optimal → price_alerts (below_min), алерт ℹ️
  │   └── promoPrice < optimal × 1.10 → price_alerts, алерт 🚨
  └── pr-SKU → пропускаем (не снижаем)

ФАЗА 4 — УПРАВЛЕНИЕ АКЦИЯМИ
  ├── Сравниваем желаемое участие с promo_participations (кэш)
  ├── Фикс. скидка: добавляем все SKU (обычные + pr)
  ├── Изменяемая:
  │   ├── Обычные: promoPrice = optimal × 1.10
  │   ├── pr-SKU: promoPrice = максимум Яндекса
  │   └── Яндекс отклонил обычный SKU → алерт 🚨
  └── Обновляем promo_participations

ФАЗА 5 — ЗАПИСЬ В БД
  ├── price_history (каждый SKU: витрина, каталог, promoPrice)
  └── price_alerts (новые + обновление существующих)

ФАЗА 6 — ИТОГОВЫЙ ОТЧЁТ В TELEGRAM
  (только если есть изменения или алерты)
```

### Параметры APScheduler

```python
scheduler.add_job(
    func=self.run_cycle,
    trigger="interval",
    hours=3,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=300,
    id="pricing_agent_cycle",
)
```

---

## Яндекс Маркет API

### Эндпоинты

| Действие | Метод | Эндпоинт |
|---|---|---|
| Витринные цены (отчёт) | POST | `v2/reports/goods-prices/generate` |
| Статус отчёта | GET | `v2/reports/info/{reportId}` |
| Скачать отчёт | GET | URL из статуса отчёта |
| Список акций | POST | `v2/businesses/{id}/promos` |
| SKU в акциях | POST | `v2/businesses/{id}/promos/offers` |
| Обновить цены каталога | POST | `v2/businesses/{id}/offer-prices/updates` |
| Обновить promoPrice | POST | `v2/businesses/{id}/promos/offers/update` |

### Паттерн асинхронного отчёта

```python
async def fetch_storefront_prices(business_id, token) -> dict[str, Decimal]:
    # 1. Запустить генерацию
    report_id = await generate_prices_report(business_id, token)
    
    # 2. Polling до готовности
    for _ in range(10):  # 10 × 30 сек = 5 мин
        await asyncio.sleep(30)
        status = await get_report_status(report_id, token)
        if status["status"] == "DONE":
            return await download_and_parse_report(status["file"])
        if status["status"] == "FAILED":
            raise ReportGenerationError(status)
    
    raise ReportTimeoutError(report_id)
```

---

## Изменения модели данных

### `market_products` — добавить поле

```python
is_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
```

### `price_history` — добавить поле

```python
promo_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
```

### Новая таблица `promo_participations`

```python
class PromoParticipation(Base):
    __tablename__ = "promo_participations"

    id:          Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id:  Mapped[uuid.UUID]  # FK market_products
    promo_id:    Mapped[str]         # ID акции Яндекса
    promo_type:  Mapped[str]         # "fixed_discount" / "direct_discount"
    promo_price: Mapped[Optional[Decimal]]  # null для фикс. акций
    discount_pct: Mapped[Optional[Decimal]] # % для фикс. акций
    updated_at:  Mapped[datetime]
```

**Назначение:** локальный кэш — сравниваем с ним, API вызываем только при изменениях.

### Alembic миграция — одна ревизия

1. `ALTER TABLE market_products ADD COLUMN is_pr BOOLEAN NOT NULL DEFAULT FALSE`
2. `ALTER TABLE price_history ADD COLUMN promo_price NUMERIC(12,2)`
3. `CREATE TABLE promo_participations ...`

### Sheets — добавить колонку `is_pr`

Текущий формат `Товары!A2:F` расширяется до `A2:G`:
```
A: sku | B: name | C: catalog_price | D: crossed_price |
E: min_price | F: optimal_price | G: is_pr (TRUE/FALSE или pr/-)
```

`sheets_loader.py` обновляется: читает колонку G, пишет в `market_products.is_pr`.
Детальная структура Grist-таблиц — отдельная задача.

---

## Telegram алерты

### Принцип

- Автоматические изменения → только итоговый отчёт (раз в цикл)
- Ситуации требующие внимания → отдельное сообщение немедленно

### Итоговый отчёт (только если есть изменения)

```
📊 Pricing Agent — цикл завершён

✅ Синхронизировано цен: 12 SKU
✅ promoPrice скорректирован: 5 SKU (−100–150₽)

⚠️ Требуют внимания: 3 SKU
  — Ромашка 51шт: promoPrice ниже порога (Я не разрешает ≥1100₽)
  — Роза 60см красная: витрина 890₽ < optimal 1000₽ ℹ️
  — Тюльпан белый: Яндекс отклонил участие в акции
```

### Срочный алерт — promoPrice ниже порога

```
🚨 SKU в акции ниже минимума

Ромашка 51шт (артикул: 123456)
  Акция: Скидки до 30% (ID: promo_789)
  Яндекс разрешает: от 900₽
  Наш минимум: 1100₽ (optimal × 1.10)

Действие: добавить вручную через ЛК или пропустить эту акцию.
```

### Информационный алерт — витрина ниже optimal

```
ℹ️ Витрина ниже optimal (Яндекс платит разницу)

Роза 60см красная: витрина 890₽ / optimal 1000₽
  Наш promoPrice: 1150₽ ✓
  Разницу 110₽ покрывает Яндекс.
```

### Алерт — риск карантина (единственный с кнопками)

```
⚠️ Риск карантина

Пион белый: текущая цена 2500₽ → новая 1800₽ (−28%)
Яндекс может скрыть товар до ручного подтверждения в кабинете.

[Да, обновить]  [Пропустить]
```

---

## Обработка ошибок

**Принцип:** частичный успех лучше полного провала. Каждая фаза независима.

| Ситуация | Поведение |
|---|---|
| Отчёт витрины не пришёл (таймаут 5 мин) | Пропустить фазу 3, продолжить остальные, алерт ⚠️ |
| API rate limit | Retry × 3, exponential backoff |
| Яндекс вернул ошибку карантина | Алерт с кнопками, не применять автоматически |
| DB упала при записи | Логировать в events_log, алерт, продолжить |
| Предыдущий цикл ещё работает | `max_instances=1` — пропустить запуск |
| Любая ошибка в фазе | Лог + events_log, включить в итоговый отчёт |

---

## Визуализация (Grist)

Grist подключён к PostgreSQL напрямую (уже в Docker Compose). Pricing Agent только пишет в таблицы — никакой дополнительной интеграции не нужно.

Вкладки для будущей настройки:
- **Цены сейчас** — `market_products` (current state)
- **История цен** — `price_history`
- **Алерты** — `price_alerts`
- **Участие в акциях** — `promo_participations`

Графики, формулы и визуализация — отдельная задача после реализации агента.

---

## Порядок разработки

1. Alembic миграция (is_pr, promo_price, promo_participations)
2. Обновить sheets_loader: читать колонку G (is_pr)
3. `market_api.py` — все эндпоинты (отчёт, акции, обновления)
4. `price_engine.py` — бизнес-логика (расчёты, решения)
5. `agent.py` — APScheduler + оркестрация фаз
6. Telegram алерты + кнопка карантина в owner_bot
7. Подключить в main.py

---

## Будущие задачи (не в MVP)

- Автоматизация буст-кампаний для pr-SKU
- Динамический шаг корректировки promoPrice на основе накопленной статистики
- Аналитика: реакция Яндекса на изменения promoPrice
- Графики и дашборды в Grist
