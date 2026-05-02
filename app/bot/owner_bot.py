from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from app.config import settings

owner_router = Router()


@owner_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("BUDS администратор готов к работе.")


@owner_router.message(Command("status"))
async def cmd_status(message: Message):
    await message.answer("Статус: онлайн.")


def register_order_callbacks(order_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith(("ready_now:", "auto_5min:"))
    )
    async def handle_order_callback(callback: CallbackQuery):
        await order_agent.handle_button_callback(callback)


def register_pricing_callbacks(pricing_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith(("price_quarantine_confirm:", "price_quarantine_skip:"))
    )
    async def handle_quarantine_callback(callback: CallbackQuery):
        await callback.answer()
        action, sku = callback.data.split(":", 1)
        if action == "price_quarantine_confirm":
            await pricing_agent.apply_quarantine_update(sku)
            await callback.message.edit_text(f"✅ Цена для {sku} обновлена в Маркете.")
        else:
            await callback.message.edit_text(f"⏭ Обновление {sku} пропущено.")


def register_stock_commands(flower_stock_agent) -> None:
    @owner_router.message()
    async def handle_stock_message(message: Message):
        if message.from_user is None:
            return
        response = await flower_stock_agent.handle_telegram_message(message.text or "")
        if response:
            await message.answer(response)


def register_eucalyptus_callbacks(flower_stock_agent) -> None:
    @owner_router.callback_query(
        lambda c: c.data and c.data.startswith("evk_restock:")
    )
    async def handle_evk_callback(callback: CallbackQuery):
        await callback.answer()
        qty_g = int(callback.data.split(":", 1)[1])
        await flower_stock_agent.handle_eucalyptus_callback(qty_g)
        label = f"{qty_g}г добавлено" if qty_g else "Не добавлять"
        await callback.message.edit_text(f"✅ {label}")


def create_owner_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher()
    dp.include_router(owner_router)
    return bot, dp
