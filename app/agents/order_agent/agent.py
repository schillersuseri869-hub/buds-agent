import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.orders import Order
from app.models.florists import Florist
from app.models.market_products import MarketProduct
from app.agents.order_agent import market_api
from app.agents.flower_stock import stock_ops
from app.agents.flower_stock import market_api as stock_market_api

logger = logging.getLogger(__name__)

_REDIS_BTN_TTL = 7200  # 2 hours

_EVKALIPT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="200г", callback_data="evk_restock:200"),
        InlineKeyboardButton(text="400г", callback_data="evk_restock:400"),
        InlineKeyboardButton(text="600г", callback_data="evk_restock:600"),
    ],
    [InlineKeyboardButton(text="Не добавлять", callback_data="evk_restock:0")],
])


async def _sleep_until(target: datetime) -> None:
    delay = (target - datetime.now(timezone.utc)).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


class OrderAgent:
    def __init__(
        self,
        redis: Redis,
        db_factory: async_sessionmaker,
        owner_bot: Bot,
        florist_bot: Bot | None,
        event_bus,
        settings,
        flower_stock_agent=None,  # kept for backwards compat, unused
    ):
        self._redis = redis
        self._db_factory = db_factory
        self._owner_bot = owner_bot
        self._florist_bot = florist_bot
        self._event_bus = event_bus
        self._settings = settings
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._last_packaging_warnings: set[str] = set()

    async def _update_storefront(self) -> None:
        try:
            async with self._db_factory() as db:
                stocks, warnings = await stock_ops.compute_available_stocks(db)
            await stock_market_api.update_stocks(
                self._settings.market_campaign_id,
                self._settings.market_api_token,
                self._settings.market_warehouse_id,
                stocks,
            )
            new_warnings = set(warnings)
            for w in sorted(new_warnings - self._last_packaging_warnings):
                await self._alert(w)
            self._last_packaging_warnings = new_warnings
        except Exception as exc:
            logger.error("_update_storefront failed: %s", exc)
            await self._alert(f"Ошибка обновления витрины Маркета: {exc}")

    async def _alert(self, message: str) -> None:
        try:
            await self._owner_bot.send_message(self._settings.owner_telegram_id, message)
        except Exception as exc:
            logger.error("Failed to send alert: %s", exc)

    async def _notify_all(self, text: str, keyboard=None) -> list[tuple]:
        messages = []
        try:
            msg = await self._owner_bot.send_message(
                self._settings.owner_telegram_id, text, reply_markup=keyboard
            )
            messages.append((self._settings.owner_telegram_id, msg.message_id, "owner"))
        except Exception as exc:
            logger.error("Failed to notify owner: %s", exc)

        if self._florist_bot:
            async with self._db_factory() as db:
                result = await db.execute(select(Florist).where(Florist.active == True))  # noqa: E712
                florists = list(result.scalars().all())
            for florist in florists:
                try:
                    msg = await self._florist_bot.send_message(
                        florist.telegram_id, text, reply_markup=keyboard
                    )
                    messages.append((florist.telegram_id, msg.message_id, "florist"))
                except Exception as exc:
                    logger.error("Failed to notify florist %s: %s", florist.telegram_id, exc)
        return messages

    async def _run_t50(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Заказ готов",
                callback_data=f"ready_now:{order_id}",
            ),
            InlineKeyboardButton(
                text="⏰ Авто через 5 мин",
                callback_data=f"auto_5min:{order_id}",
            ),
        ]])

        messages = await self._notify_all(
            f"⏰ Заказ #{market_order_id} — осталось 7 минут! Выберите действие:",
            keyboard=keyboard,
        )

        redis_data = json.dumps({
            "messages": [list(m) for m in messages],
            "market_order_id": market_order_id,
        })
        await self._redis.setex(f"order:buttons:{order_id}", _REDIS_BTN_TTL, redis_data)

    async def _run_t55(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
            order = result.scalar_one_or_none()
        if order is None or order.status != "waiting":
            return

        success = False
        for attempt in range(3):
            try:
                await market_api.set_order_ready(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                success = True
                break
            except Exception as exc:
                logger.error("set_order_ready attempt %d failed: %s", attempt + 1, exc)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

        if not success:
            await self._alert(
                f"⚠️ Не удалось установить статус 'Готов' для заказа #{market_order_id}."
                f" Зайдите в Маркет вручную."
            )
            return

        confirmed = False
        for _ in range(3):
            await asyncio.sleep(40)
            if order_id not in self._tasks:
                return
            try:
                status = await market_api.get_order_status(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                if status == "READY_TO_SHIP":
                    confirmed = True
                    break
            except Exception as exc:
                logger.error("get_order_status failed: %s", exc)

        if confirmed:
            await self._event_bus.publish("order.ready", {
                "order_id": order_id,
                "market_order_id": market_order_id,
                "source": "auto_t55",
            })
        else:
            await self._alert(
                f"🚨 Статус заказа #{market_order_id} не подтверждён Маркетом!"
                f" ~180 сек — зайдите в Маркет вручную."
            )

    async def _run_t57(self, order_id: str, market_order_id: str, fire_at: datetime) -> None:
        await _sleep_until(fire_at)
        if order_id not in self._tasks:
            return

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
            order = result.scalar_one_or_none()
            if order is None or order.status != "waiting":
                return
            order.status = "timed_out"
            await db.commit()

        self._tasks.pop(order_id, None)
        await self._event_bus.publish("order.timeout", {
            "order_id": order_id,
            "market_order_id": market_order_id,
        })

        await self._clear_button_messages(order_id)
        await self._redis.delete(f"order:buttons:pressed:{order_id}")

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="✅ Заказ готов",
                callback_data=f"ready_now:{order_id}",
            ),
        ]])
        messages = await self._notify_all(
            f"⚠️ Время для сборки заказа #{market_order_id} истекло! Нажмите когда заказ будет готов:",
            keyboard=keyboard,
        )
        redis_data = json.dumps({
            "messages": [list(m) for m in messages],
            "market_order_id": market_order_id,
        })
        await self._redis.setex(f"order:buttons:{order_id}", _REDIS_BTN_TTL, redis_data)

    def _schedule_timers(self, order_id: str, market_order_id: str, deadline: datetime) -> None:
        now = datetime.now(timezone.utc)
        t50 = deadline - timedelta(minutes=7)
        t55 = deadline - timedelta(minutes=2)
        t57 = deadline

        tasks = []
        if t50 > now:
            tasks.append(asyncio.create_task(self._run_t50(order_id, market_order_id, t50)))
        if t55 > now:
            tasks.append(asyncio.create_task(self._run_t55(order_id, market_order_id, t55)))
        if t57 > now:
            tasks.append(asyncio.create_task(self._run_t57(order_id, market_order_id, t57)))

        if tasks:
            self._tasks[order_id] = tasks

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

        deadline = datetime.now(timezone.utc) + timedelta(minutes=57)

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order is None:
                logger.error("Order not found in DB: %s", order_id_str)
                return
            order.timer_deadline = deadline
            await db.commit()

        items: list[dict] = []
        items_lines = ""
        try:
            items = await market_api.get_order_items(
                market_order_id,
                self._settings.market_campaign_id,
                self._settings.market_api_token,
            )
            if items:
                async with self._db_factory() as db:
                    result = await db.execute(select(MarketProduct))
                    names = {p.market_sku: p.name for p in result.scalars().all()}
                lines = [
                    f"{it['sku']} × {it['count']} — {names.get(it['sku'], '?')}"
                    for it in items
                ]
                items_lines = "\n" + "\n".join(lines) + "\n"
        except Exception as exc:
            logger.warning("Could not fetch order items for notification: %s", exc)

        await self._notify_all(f"🌸 Новый заказ!{items_lines}\n#{market_order_id}")
        self._schedule_timers(order_id_str, market_order_id, deadline)

        if items:
            try:
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
                                logger.error("eucalyptus alert to florist failed: %s", exc)
            except Exception as exc:
                logger.error("reserve_materials failed for %s: %s", market_order_id, exc)

    def cancel_timers(self, order_id: str) -> None:
        tasks = self._tasks.pop(order_id, [])
        for task in tasks:
            task.cancel()

    async def _clear_button_messages(self, order_id: str) -> None:
        raw = await self._redis.get(f"order:buttons:{order_id}")
        if not raw:
            return
        button_data = json.loads(raw)
        for entry in button_data.get("messages", []):
            chat_id, message_id, bot_type = entry
            bot = self._owner_bot if bot_type == "owner" else self._florist_bot
            if bot is None:
                continue
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=message_id, reply_markup=None
                )
            except Exception as exc:
                logger.error("Failed to clear buttons chat=%s msg=%s: %s", chat_id, message_id, exc)

    async def handle_order_status(self, channel: str, data: dict) -> None:
        order_id_str = data.get("order_id")
        if not order_id_str:
            logger.error("order status event missing order_id: %s", data)
            return

        status_map = {
            "order.ready": "ready",
            "order.cancelled": "cancelled",
            "order.shipped": "shipped",
            "order.delivered": "delivered",
        }
        new_status = status_map.get(channel)
        if new_status is None:
            return

        try:
            order_uuid = uuid.UUID(order_id_str)
        except ValueError:
            logger.error("Invalid order_id UUID: %s", order_id_str)
            return

        market_order_id = data.get("market_order_id", "")

        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.id == order_uuid))
            order = result.scalar_one_or_none()
            if order is None:
                return
            if not market_order_id:
                market_order_id = order.market_order_id
            # order.ready допускает переход из waiting и timed_out (флорист нажал кнопку после просрочки)
            allowed_from = {"waiting", "timed_out"} if channel == "order.ready" else {"waiting"}
            if order.status in allowed_from:
                order.status = new_status
                await db.commit()

        self.cancel_timers(order_id_str)
        await self._clear_button_messages(order_id_str)

        if channel == "order.ready":
            try:
                async with self._db_factory() as db:
                    await stock_ops.debit_materials(db, order_uuid)
                    cost = await stock_ops.compute_order_cost(db, order_uuid)
                    result2 = await db.execute(select(Order).where(Order.id == order_uuid))
                    order2 = result2.scalar_one_or_none()
                    if order2:
                        order2.estimated_cost = cost
                        await db.commit()
                await self._update_storefront()
            except Exception as exc:
                logger.error("debit_materials failed for %s: %s", order_id_str, exc)
        elif channel == "order.cancelled":
            try:
                async with self._db_factory() as db:
                    await stock_ops.release_materials(db, order_uuid)
                await self._update_storefront()
            except Exception as exc:
                logger.error("release_materials failed for %s: %s", order_id_str, exc)
        elif channel == "order.shipped":
            await self._notify_all(f"🚗 Заказ #{market_order_id} передан курьеру — в доставке")
        elif channel == "order.delivered":
            await self._notify_all(f"✅ Заказ #{market_order_id} доставлен")

    async def recover_timers(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._db_factory() as db:
            result = await db.execute(select(Order).where(Order.status == "waiting"))
            orders = list(result.scalars().all())

        for order in orders:
            if order.timer_deadline is None:
                continue
            order_id = str(order.id)
            if order.timer_deadline <= now:
                async with self._db_factory() as db:
                    result = await db.execute(select(Order).where(Order.id == order.id))
                    fresh = result.scalar_one_or_none()
                    if fresh and fresh.status == "waiting":
                        fresh.status = "timed_out"
                        await db.commit()
                await self._event_bus.publish("order.timeout", {
                    "order_id": order_id,
                    "market_order_id": order.market_order_id,
                })
                await self._redis.delete(f"order:buttons:pressed:{order_id}")
                keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(
                        text="✅ Заказ готов",
                        callback_data=f"ready_now:{order_id}",
                    ),
                ]])
                messages = await self._notify_all(
                    f"⚠️ Время для сборки заказа #{order.market_order_id} истекло! Нажмите когда заказ будет готов:",
                    keyboard=keyboard,
                )
                redis_data = json.dumps({
                    "messages": [list(m) for m in messages],
                    "market_order_id": order.market_order_id,
                })
                await self._redis.setex(f"order:buttons:{order_id}", _REDIS_BTN_TTL, redis_data)
            else:
                self._schedule_timers(order_id, order.market_order_id, order.timer_deadline)

    async def handle_button_callback(self, callback) -> None:
        data = callback.data
        action, order_id = data.split(":", 1)

        pressed_key = f"order:buttons:pressed:{order_id}"
        was_first = await self._redis.set(pressed_key, "1", nx=True, ex=_REDIS_BTN_TTL)
        if not was_first:
            await callback.answer("Уже принято другим пользователем")
            return

        await callback.answer("Принято!")

        raw = await self._redis.get(f"order:buttons:{order_id}")
        market_order_id = ""
        messages = []
        if raw:
            btn_data = json.loads(raw)
            market_order_id = btn_data.get("market_order_id", "")
            messages = btn_data.get("messages", [])

        if action == "ready_now":
            self.cancel_timers(order_id)
            action_text = "Заказ готов"
            try:
                await market_api.set_order_ready(
                    market_order_id,
                    self._settings.market_campaign_id,
                    self._settings.market_api_token,
                )
                await self._event_bus.publish("order.ready", {
                    "order_id": order_id,
                    "market_order_id": market_order_id,
                    "source": "button",
                })
            except Exception as exc:
                logger.error("set_order_ready failed from button: %s", exc)
                await self._alert(
                    f"⚠️ Не удалось установить статус 'Готов' для заказа #{market_order_id}."
                    f" Зайдите в Маркет вручную."
                )
        else:
            action_text = "Принято"

        for entry in messages:
            chat_id, message_id, bot_type = entry
            bot = self._owner_bot if bot_type == "owner" else self._florist_bot
            if bot is None:
                continue
            try:
                await bot.edit_message_text(
                    text=f"✅ {action_text} — принято",
                    chat_id=chat_id,
                    message_id=message_id,
                )
            except Exception as exc:
                logger.error("Failed to edit button message: %s", exc)
