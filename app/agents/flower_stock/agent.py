import logging
import re
import uuid
from decimal import Decimal

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.orders import Order
from app.agents.flower_stock import market_api, stock_ops

logger = logging.getLogger(__name__)

_ARRIVAL_RE = re.compile(
    r"пришло\s+(\d+(?:[.,]\d+)?)\s+(.+?)\s+по\s+(\d+(?:[.,]\d+)?)₽?",
    re.IGNORECASE,
)
_EXTRA_DEBIT_RE = re.compile(
    r"дополнительно\s+списал\s+(\d+(?:[.,]\d+)?)\s+(.+?)\s+к\s+заказу\s+#(\S+)",
    re.IGNORECASE,
)
_SPOILAGE_RE = re.compile(
    r"^списал\s+(\d+(?:[.,]\d+)?)\s+(.+)",
    re.IGNORECASE,
)

_EVKALIPT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="200г", callback_data="evk_restock:200"),
        InlineKeyboardButton(text="400г", callback_data="evk_restock:400"),
        InlineKeyboardButton(text="600г", callback_data="evk_restock:600"),
    ],
    [InlineKeyboardButton(text="Не добавлять", callback_data="evk_restock:0")],
])


def _to_decimal(s: str) -> Decimal:
    return Decimal(s.replace(",", "."))


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


class FlowerStockAgent:
    def __init__(
        self,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        settings,
        florist_bot: Bot | None = None,
    ):
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._settings = settings
        self._florist_bot = florist_bot

    async def push_write_off_to_grist(self, material, wo_type: str, quantity) -> None:
        if not self._settings.grist_api_key:
            return
        try:
            from app.agents.flower_stock.sheets_loader import push_write_off_to_grist
            await push_write_off_to_grist(
                self._settings.grist_url,
                self._settings.grist_doc_id,
                self._settings.grist_api_key,
                material.name,
                wo_type,
                quantity,
                material.unit,
                material.cost_per_unit,
            )
        except Exception as exc:
            logger.error("push_write_off_to_grist(%s) failed: %s", material.name, exc)

    async def sync_to_grist(self, material) -> None:
        """Push updated physical_stock to Grist and send low-stock alert if needed."""
        if not material.grist_row_id or not self._settings.grist_api_key:
            return
        try:
            from app.agents.flower_stock.sheets_loader import push_material_to_grist
            await push_material_to_grist(
                self._settings.grist_url,
                self._settings.grist_doc_id,
                self._settings.grist_api_key,
                material.grist_row_id,
                material.physical_stock,
            )
        except Exception as exc:
            logger.error("sync_to_grist(%s) failed: %s", material.name, exc)
        if material.min_stock is not None and material.physical_stock <= material.min_stock:
            await self._alert_all(
                f"⚠️ «{material.name}»: остаток {_fmt(material.physical_stock)} {material.unit} "
                f"(минимум {_fmt(material.min_stock)} {material.unit})"
            )

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, message)
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)

    async def _alert_all(self, message: str, reply_markup=None) -> None:
        """Send message to owner and florist (if configured)."""
        try:
            await self._owner_bot.send_message(
                self._settings.owner_telegram_id, message, reply_markup=reply_markup
            )
        except Exception as exc:
            logger.error("Failed to send owner alert: %s", exc)
        if self._florist_bot and self._settings.florist_telegram_id:
            try:
                await self._florist_bot.send_message(
                    self._settings.florist_telegram_id, message, reply_markup=reply_markup
                )
            except Exception as exc:
                logger.error("Failed to send florist alert: %s", exc)

    async def get_stock_report(self) -> str:
        from sqlalchemy import select
        from app.models.raw_materials import RawMaterial
        async with self._db_factory() as db:
            result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
            materials = list(result.scalars().all())
        if not materials:
            return "Склад пуст."
        lines = ["📦 Склад:\n"]
        for m in materials:
            if m.physical_stock == 0:
                continue
            net = m.physical_stock - m.reserved
            reserved_str = f" (резерв: {_fmt(m.reserved)})" if m.reserved > 0 else ""
            lines.append(f"• {m.name} — {_fmt(net)} {m.unit}{reserved_str}")
        return "\n".join(lines) if len(lines) > 1 else "Склад пуст."

    async def _update_storefront(self) -> None:
        try:
            async with self._db_factory() as db:
                stocks = await stock_ops.compute_available_stocks(db)
            await market_api.update_stocks(
                self._settings.market_campaign_id,
                self._settings.market_api_token,
                self._settings.market_warehouse_id,
                stocks,
            )
        except Exception as exc:
            logger.error("_update_storefront failed: %s", exc)
            await self._alert(f"Ошибка обновления витрины Маркета: {exc}")

    async def handle_eucalyptus_callback(self, qty_g: int) -> None:
        """Handle florist/owner restock button tap. qty_g=0 means 'do not restock'."""
        if qty_g == 0:
            return
        try:
            async with self._db_factory() as db:
                await stock_ops.set_eucalyptus_stock(db, Decimal(qty_g))
            await self._update_storefront()
            await self._alert_all(
                f"✅ Эвкалипт: {qty_g}г. Позиции с эвкалиптом возвращены на витрину."
            )
        except Exception as exc:
            logger.error("handle_eucalyptus_callback failed: %s", exc)
            await self._alert(f"Ошибка при обновлении эвкалипта: {exc}")

    async def handle_order_created(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        market_order_id = data.get("market_order_id")
        if not order_id_str or not market_order_id:
            logger.error("order.created missing fields: %s", data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        try:
            items = await market_api.get_order_items(
                market_order_id,
                self._settings.market_campaign_id,
                self._settings.market_api_token,
            )
        except Exception as exc:
            logger.error("get_order_items failed for %s: %s", market_order_id, exc)
            await self._alert(f"Ошибка получения состава заказа #{market_order_id}")
            return

        async with self._db_factory() as db:
            await stock_ops.save_order_items(db, order_uuid, items)

        async with self._db_factory() as db:
            await stock_ops.reserve_materials(db, order_uuid, items)

        await self._update_storefront()

        has_e_items = any("-e" in item.get("sku", "") for item in items)
        if has_e_items:
            async with self._db_factory() as db:
                low = await stock_ops.is_eucalyptus_low(db)
            if low:
                await self._alert("⚠️ Эвкалипт заканчивается.")
                if self._florist_bot and self._settings.florist_telegram_id:
                    try:
                        await self._florist_bot.send_message(
                            self._settings.florist_telegram_id,
                            "⚠️ Эвкалипт заканчивается. Сколько осталось в холодильнике?",
                            reply_markup=_EVKALIPT_KEYBOARD,
                        )
                    except Exception as exc:
                        logger.error("Failed to send eucalyptus alert to florist: %s", exc)

    async def handle_order_ready(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("order.ready missing order_id: %s", data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        async with self._db_factory() as db:
            await stock_ops.debit_materials(db, order_uuid)
            cost = await stock_ops.compute_order_cost(db, order_uuid)
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order:
                order.estimated_cost = cost
                await db.commit()

        await self._update_storefront()

    async def handle_order_released(self, channel: str, data: dict) -> None:
        """Handles both order.cancelled and order.timeout."""
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("%s missing order_id: %s", channel, data)
            return
        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        async with self._db_factory() as db:
            await stock_ops.release_materials(db, order_uuid)

        await self._update_storefront()

    def _parse_command(self, text: str) -> dict | None:
        """Parse a Telegram stock command. Returns parsed dict or None."""
        m = _ARRIVAL_RE.search(text)
        if m:
            return {
                "type": "arrival",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
                "cost_per_unit": _to_decimal(m.group(3)),
            }
        m = _EXTRA_DEBIT_RE.search(text)
        if m:
            return {
                "type": "extra_debit",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
                "order_ref": m.group(3),
            }
        m = _SPOILAGE_RE.search(text)
        if m:
            return {
                "type": "spoilage",
                "quantity": _to_decimal(m.group(1)),
                "material_name": m.group(2).strip(),
            }
        return None

    async def handle_telegram_message(self, text: str) -> str | None:
        """Parse and execute a stock command. Returns response text or None if unrecognized."""
        parsed = self._parse_command(text)
        if parsed is None:
            return None

        async with self._db_factory() as db:
            material = await stock_ops.find_material_by_name(db, parsed["material_name"])

        if material is None:
            return f"Сырьё «{parsed['material_name']}» не найдено в базе."

        cmd_type = parsed["type"]

        if cmd_type == "arrival":
            async with self._db_factory() as db:
                mat = await stock_ops.record_arrival(
                    db, material.id, parsed["quantity"], parsed["cost_per_unit"]
                )
            await self._update_storefront()
            return (
                f"✅ Приход: {_fmt(parsed['quantity'])} {mat.unit} «{mat.name}» "
                f"по {_fmt(parsed['cost_per_unit'])}₽. Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
            )

        if cmd_type == "spoilage":
            async with self._db_factory() as db:
                mat = await stock_ops.record_spoilage(db, material.id, parsed["quantity"])
            await self._update_storefront()
            return (
                f"✅ Списано: {_fmt(parsed['quantity'])} {mat.unit} «{mat.name}». "
                f"Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
            )

        if cmd_type == "extra_debit":
            order_ref = parsed["order_ref"]
            async with self._db_factory() as db:
                result = await db.execute(
                    select(Order).where(Order.market_order_id == order_ref)
                )
                order = result.scalar_one_or_none()
                if order is None:
                    return f"Заказ #{order_ref} не найден."
                mat = await stock_ops.record_extra_debit(
                    db,
                    material.id,
                    order.id,
                    parsed["quantity"],
                    note=f"доп. списание к заказу #{order_ref}",
                )
            await self._update_storefront()
            return (
                f"✅ Доп. списание: {_fmt(parsed['quantity'])} {mat.unit} «{mat.name}» "
                f"к заказу #{order_ref}."
            )

        return None
