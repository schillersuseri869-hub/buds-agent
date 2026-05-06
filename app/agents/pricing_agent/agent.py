import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from aiogram import Bot
import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.pricing_agent import market_api
from app.agents.pricing_agent.market_api import PricesReport
from app.agents.pricing_agent.price_engine import (
    compute_catalog_update,
    evaluate_storefront,
    compute_promo_floor,
    CatalogUpdate,
    StorefrontDecision,
)
from app.models.market_products import MarketProduct
from app.models.price_history import PriceHistory
from app.models.price_alerts import PriceAlert
from app.models.promo_participations import PromoParticipation
from app.models.promo import Promo

logger = logging.getLogger(__name__)


def _parse_dt(value: str | None):
    if value is None:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, AttributeError):
        return None


@dataclass
class CycleResult:
    catalog_synced: int = 0
    promo_adjusted: int = 0
    alerts: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    quarantine_pending: list[CatalogUpdate] = field(default_factory=list)


class PricingAgent:
    def __init__(
        self,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
        scheduler,
    ):
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings
        self._scheduler = scheduler

    def schedule(self) -> None:
        self._scheduler.add_job(
            func=self.run_cycle,
            trigger="interval",
            hours=3,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            id="pricing_agent_cycle",
        )
        logger.info("PricingAgent scheduled every 3 hours")

    async def _alert(self, text: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, text)
        except Exception as exc:
            logger.error("Alert send failed: %s", exc)

    async def _load_products(self) -> list[MarketProduct]:
        async with self._db_factory() as db:
            result = await db.execute(select(MarketProduct))
            return result.scalars().all()

    async def _load_promo_cache(self) -> dict[str, dict[str, Decimal]]:
        """Returns {promo_id: {product_id_str: promo_price}}."""
        async with self._db_factory() as db:
            result = await db.execute(select(PromoParticipation))
            cache: dict[str, dict[str, Decimal]] = {}
            for pp in result.scalars().all():
                cache.setdefault(pp.promo_id, {})[str(pp.product_id)] = pp.promo_price
        return cache

    async def _sync_promos(self, available_promos: list[dict]) -> None:
        async with self._db_factory() as db:
            for promo in available_promos:
                db.merge(Promo(
                    promo_id=promo.get("id") or promo.get("promoId"),
                    name=promo.get("name", ""),
                    type=promo.get("mechanicsType", ""),
                    starts_at=_parse_dt(promo.get("startDate")),
                    ends_at=_parse_dt(promo.get("endDate")),
                    updated_at=datetime.now(timezone.utc),
                ))
            await db.commit()

    async def _save_price_history(
        self,
        products: list[MarketProduct],
        report: PricesReport,
        promo_prices: dict[str, Decimal],
    ) -> None:
        async with self._db_factory() as db:
            for prod in products:
                db.add(PriceHistory(
                    product_id=prod.id,
                    catalog_price=prod.catalog_price,
                    storefront_price=report.storefront.get(prod.market_sku),
                    min_price=prod.min_price,
                    optimal_price=prod.optimal_price,
                    promo_price=promo_prices.get(prod.market_sku),
                ))
            await db.commit()

    async def _update_storefront_prices(
        self, products: list[MarketProduct], storefront_prices: dict[str, Decimal]
    ) -> None:
        async with self._db_factory() as db:
            for prod in products:
                price = storefront_prices.get(prod.market_sku)
                if price is not None:
                    prod.storefront_price = price
                    db.add(prod)
            await db.commit()

    async def _sync_pricing_grist(self) -> None:
        grist_url = self._settings.grist_url.rstrip("/")
        doc_id = self._settings.grist_doc_id
        api_key = self._settings.grist_api_key
        if not doc_id or not api_key:
            logger.debug("Grist not configured — skipping PricingReport sync")
            return

        async with self._db_factory() as db:
            result = await db.execute(text("SELECT * FROM v_pricing_report"))
            rows = result.mappings().all()

        def _num(v):
            return float(v) if v is not None else None

        def _str(v):
            return str(v) if v is not None else ""

        base = f"{grist_url}/api/docs/{doc_id}/tables/PricingReport"
        headers = {"Authorization": f"Bearer {api_key}"}

        def _row_fields(row) -> dict:
            fields: dict = {
                "name": _str(row["name"]),
                "market_sku": _str(row["market_sku"]),
                "promo_name": _str(row["promo_name"]),
                "promo_type": _str(row["promo_type"]),
                "ends_at": _str(row["ends_at"]),
                "status": _str(row["status"]),
            }
            for col in ("catalog_price", "min_promo_price", "storefront_price",
                        "optimal_price", "promo_price", "discount_pct"):
                v = _num(row[col])
                if v is not None:
                    fields[col] = v
            return fields

        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(f"{base}/records", headers=headers)
            r.raise_for_status()
            existing_ids = [rec["id"] for rec in r.json().get("records", [])]

            if existing_ids:
                r = await client.post(f"{base}/data/delete", headers=headers, json=existing_ids)
                if not r.is_success:
                    logger.error("Grist delete failed %s: %s", r.status_code, r.text[:500])
                    r.raise_for_status()

            if rows:
                records = [{"fields": _row_fields(row)} for row in rows]
                chunk_size = 200
                for i in range(0, len(records), chunk_size):
                    chunk = records[i:i + chunk_size]
                    r = await client.post(f"{base}/records", headers=headers,
                                         json={"records": chunk})
                    if not r.is_success:
                        logger.error("Grist insert failed %s: %s", r.status_code, r.text[:500])
                        r.raise_for_status()

        logger.info("Grist PricingReport synced: %d rows", len(rows))

    async def _save_alert(self, product_id, alert_type: str, message: str) -> None:
        async with self._db_factory() as db:
            db.add(PriceAlert(
                product_id=product_id,
                type=alert_type,
                message=message,
            ))
            await db.commit()

    # ─── Phase 2: Catalog sync ───────────────────────────────────────────────

    async def _phase_catalog_sync(
        self,
        products: list[MarketProduct],
        report: PricesReport,
        result: CycleResult,
    ) -> None:
        safe_updates: list[dict] = []

        for prod in products:
            market_catalog = report.catalog.get(prod.market_sku)
            if market_catalog is None:
                market_catalog = prod.catalog_price
            update = compute_catalog_update(
                sku=prod.market_sku,
                db_catalog=prod.catalog_price,
                db_crossed=prod.crossed_price,
                db_optimal=prod.optimal_price,
                market_catalog=market_catalog,
            )
            if update is None:
                continue
            if update.quarantine_risk:
                result.quarantine_pending.append(update)
                continue
            safe_updates.append({
                "sku": update.sku,
                "value": update.new_value,
                "discount_base": update.new_discount_base,
                "minimum_for_bestseller": update.minimum_for_bestseller,
            })

        if safe_updates:
            try:
                await market_api.update_catalog_prices(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    safe_updates,
                )
                result.catalog_synced = len(safe_updates)
            except Exception as exc:
                logger.error("Catalog sync failed: %s", exc)
                result.errors.append(f"Sync каталога: {exc}")

    # ─── Phase 3: Storefront monitoring ─────────────────────────────────────

    async def _phase_storefront(
        self,
        products: list[MarketProduct],
        report: PricesReport,
        promo_cache: dict[str, dict[str, Decimal]],
        result: CycleResult,
    ) -> None:
        current_promos: dict[str, Decimal] = {}
        for promo_data in promo_cache.values():
            for pid, price in promo_data.items():
                if price is not None:
                    current_promos[pid] = price

        promo_updates: list[tuple[str, str, Decimal]] = []  # (promo_id, sku, new_price)

        for prod in products:
            storefront = report.storefront.get(prod.market_sku)
            if storefront is None:
                continue
            current_promo = current_promos.get(str(prod.id))
            if current_promo is None:
                continue

            decision = evaluate_storefront(
                sku=prod.market_sku,
                storefront=storefront,
                optimal=prod.optimal_price,
                current_promo=current_promo,
                is_pr=prod.is_pr,
            )

            if decision.action == "lower":
                for promo_id, pdata in promo_cache.items():
                    if str(prod.id) in pdata:
                        promo_updates.append((promo_id, prod.market_sku, decision.new_promo_price))
                result.promo_adjusted += 1

            elif decision.action == "alert_below_optimal":
                msg = (
                    f"ℹ️ Витрина ниже optimal (Яндекс платит разницу)\n\n"
                    f"{prod.name}: витрина {storefront}₽ / optimal {prod.optimal_price}₽\n"
                    f"  Наш promoPrice: {current_promo}₽ ✓\n"
                    f"  Разницу {prod.optimal_price - storefront}₽ покрывает Яндекс."
                )
                result.alerts.append(f"{prod.name}: витрина {storefront}₽ < optimal")
                await self._save_alert(prod.id, "below_min", msg)

            elif decision.action == "alert_floor_breach":
                msg = (
                    f"🚨 promoPrice ниже минимума\n\n"
                    f"{prod.name}: promoPrice {current_promo}₽ < порог "
                    f"{compute_promo_floor(prod.optimal_price)}₽ (optimal × 1.10)"
                )
                result.alerts.append(f"{prod.name}: promoPrice ниже порога")
                await self._alert(msg)

        for promo_id, sku, new_price in promo_updates:
            try:
                await market_api.update_promo_offers(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    promo_id,
                    [{"sku": sku, "promo_price": new_price}],
                )
            except Exception as exc:
                logger.error("promoPrice update failed %s: %s", sku, exc)
                result.errors.append(f"promoPrice {sku}: {exc}")

    # ─── Phase 4: Promo management ───────────────────────────────────────────

    async def _phase_promo_management(
        self,
        products: list[MarketProduct],
        available_promos: list[dict],
        promo_cache: dict[str, dict[str, Decimal]],
        result: CycleResult,
    ) -> None:
        for promo in available_promos:
            promo_id = promo.get("id") or promo.get("promoId", "")
            promo_type = promo.get("mechanicsType", "")

            is_fixed = "DIRECT_DISCOUNT" not in promo_type and "CHEAPEST_AS_GIFT" not in promo_type
            is_direct = "DIRECT_DISCOUNT" in promo_type

            cached = promo_cache.get(promo_id, {})
            offers_to_add: list[dict] = []

            for prod in products:
                already_in = str(prod.id) in cached
                if already_in:
                    continue

                if is_fixed:
                    offers_to_add.append({"sku": prod.market_sku, "promo_price": None})
                elif is_direct:
                    floor = compute_promo_floor(prod.optimal_price)
                    if prod.is_pr:
                        offers_to_add.append({"sku": prod.market_sku, "promo_price": prod.catalog_price})
                    else:
                        offers_to_add.append({"sku": prod.market_sku, "promo_price": floor})

            if not offers_to_add:
                continue

            try:
                api_result = await market_api.update_promo_offers(
                    self._settings.market_business_id,
                    self._settings.market_api_token,
                    promo_id,
                    offers_to_add,
                )
                rejected_skus = {r.get("offerId") for r in api_result.get("rejected", [])}
                for rej in api_result.get("rejected", []):
                    sku = rej.get("offerId", "")
                    reason = rej.get("reason", "")
                    result.alerts.append(f"{sku}: Яндекс отклонил участие в акции ({reason})")

                now = datetime.now(timezone.utc)
                async with self._db_factory() as db:
                    for offer in offers_to_add:
                        if offer["sku"] in rejected_skus:
                            continue
                        prod = next((p for p in products if p.market_sku == offer["sku"]), None)
                        if prod is None:
                            continue
                        db.add(PromoParticipation(
                            product_id=prod.id,
                            promo_id=promo_id,
                            promo_type="fixed_discount" if is_fixed else "direct_discount",
                            promo_price=offer.get("promo_price"),
                            discount_pct=None,
                            updated_at=now,
                        ))
                    await db.commit()
            except Exception as exc:
                logger.error("Promo management failed for %s: %s", promo_id, exc)
                result.errors.append(f"Акция {promo_id}: {exc}")

    # ─── Phase 6: Telegram summary ───────────────────────────────────────────

    async def _send_summary(self, result: CycleResult) -> None:
        has_content = (
            result.catalog_synced > 0
            or result.promo_adjusted > 0
            or result.alerts
            or result.errors
            or result.quarantine_pending
        )
        if not has_content:
            return

        lines = ["📊 Pricing Agent — цикл завершён\n"]
        if result.catalog_synced:
            lines.append(f"✅ Синхронизировано цен: {result.catalog_synced} SKU")
        if result.promo_adjusted:
            lines.append(f"✅ promoPrice скорректирован: {result.promo_adjusted} SKU")
        if result.errors:
            lines.append("")
            for e in result.errors:
                lines.append(f"❌ {e}")
        if result.alerts:
            lines.append(f"\n⚠️ Требуют внимания: {len(result.alerts)} SKU")
            for a in result.alerts[:10]:
                lines.append(f"  — {a}")
            if len(result.alerts) > 10:
                lines.append(f"  … и ещё {len(result.alerts) - 10}")

        await self._alert("\n".join(lines))

        for update in result.quarantine_pending:
            await self._send_quarantine_alert(update)

    async def _send_quarantine_alert(self, update: CatalogUpdate) -> None:
        from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        text = (
            f"⚠️ Риск карантина\n\n"
            f"{update.sku}: текущая {update.new_value}₽ — резкое изменение цены\n"
            f"Яндекс может скрыть товар до ручного подтверждения.\n\n"
            f"Применить изменение?"
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="Да, обновить",
                callback_data=f"price_quarantine_confirm:{update.sku}"
            ),
            InlineKeyboardButton(
                text="Пропустить",
                callback_data=f"price_quarantine_skip:{update.sku}"
            ),
        ]])
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, text, reply_markup=keyboard
            )
        except Exception as exc:
            logger.error("Quarantine alert failed: %s", exc)

    async def apply_quarantine_update(self, sku: str) -> None:
        async with self._db_factory() as db:
            result = await db.execute(
                select(MarketProduct).where(MarketProduct.market_sku == sku)
            )
            prod = result.scalar_one_or_none()
        if prod is None:
            logger.error("apply_quarantine_update: SKU %s not found", sku)
            return
        try:
            await market_api.update_catalog_prices(
                self._settings.market_business_id,
                self._settings.market_api_token,
                [{
                    "sku": sku,
                    "value": prod.catalog_price,
                    "discount_base": prod.crossed_price,
                    "minimum_for_bestseller": prod.optimal_price,
                }],
            )
            logger.info("Quarantine update applied for %s", sku)
        except Exception as exc:
            logger.error("Quarantine update failed for %s: %s", sku, exc)
            await self._alert(f"❌ Ошибка обновления цены {sku}: {exc}")

    # ─── Main cycle ──────────────────────────────────────────────────────────

    async def run_cycle(self) -> None:
        logger.info("PricingAgent cycle started")
        result = CycleResult()

        products = await self._load_products()
        if not products:
            logger.info("No products in DB — skipping cycle")
            return

        promo_cache = await self._load_promo_cache()

        # Phase 1: Fetch prices report (catalog + storefront)
        report = PricesReport()
        try:
            report = await market_api.fetch_prices_report(
                self._settings.market_business_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("Prices report failed: %s", exc)
            result.errors.append(f"Отчёт цен недоступен: {exc}")
            await self._alert(f"⚠️ Отчёт цен недоступен — фазы 2 и 3 пропущены\n{exc}")

        # Phase 1b: Fetch available promos
        available_promos: list[dict] = []
        try:
            available_promos = await market_api.get_promos(
                self._settings.market_business_id,
                self._settings.market_api_token,
            )
            await self._sync_promos(available_promos)
        except Exception as exc:
            logger.error("get_promos failed: %s", exc)
            result.errors.append(f"Список акций недоступен: {exc}")

        # Phase 2: Catalog sync (only if report succeeded)
        if report.catalog:
            try:
                await self._phase_catalog_sync(products, report, result)
            except Exception as exc:
                logger.error("_phase_catalog_sync crashed: %s", exc)
                result.errors.append(f"Фаза 2 упала: {exc}")

        # Phase 3: Storefront monitoring (only if report succeeded)
        if report.storefront:
            try:
                await self._phase_storefront(products, report, promo_cache, result)
            except Exception as exc:
                logger.error("_phase_storefront crashed: %s", exc)
                result.errors.append(f"Фаза 3 упала: {exc}")

        # Phase 4: Promo management
        try:
            await self._phase_promo_management(products, available_promos, promo_cache, result)
        except Exception as exc:
            logger.error("_phase_promo_management crashed: %s", exc)
            result.errors.append(f"Фаза 4 упала: {exc}")

        # Phase 5: Save history + storefront prices
        try:
            product_by_id = {str(p.id): p for p in products}
            current_promos: dict[str, Decimal] = {}
            for pdata in promo_cache.values():
                for pid_str, price in pdata.items():
                    if price and pid_str in product_by_id:
                        current_promos[product_by_id[pid_str].market_sku] = price
            await self._save_price_history(products, report, current_promos)
            await self._update_storefront_prices(products, report.storefront)
        except Exception as exc:
            logger.error("Price history save failed: %s", exc)

        # Phase 6: Summary
        await self._send_summary(result)

        # Phase 7: Sync to Grist
        try:
            await self._sync_pricing_grist()
        except Exception as exc:
            logger.error("Grist PricingReport sync failed: %s", exc)

        logger.info(
            "PricingAgent cycle done: synced=%d adjusted=%d alerts=%d errors=%d",
            result.catalog_synced, result.promo_adjusted,
            len(result.alerts), len(result.errors),
        )
