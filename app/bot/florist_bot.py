from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from app.config import settings

florist_router = Router()


@florist_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Флорист подключён. Ожидаю заказы.")


def register_order_callbacks(order_agent) -> None:
    @florist_router.callback_query(
        lambda c: c.data and c.data.startswith(("ready_now:", "auto_5min:"))
    )
    async def handle_order_callback(callback: CallbackQuery):
        await order_agent.handle_button_callback(callback)


def register_eucalyptus_callbacks(flower_stock_agent) -> None:
    @florist_router.callback_query(
        lambda c: c.data and c.data.startswith("evk_restock:")
    )
    async def handle_evk_callback(callback: CallbackQuery):
        await callback.answer()
        qty_g = int(callback.data.split(":", 1)[1])
        await flower_stock_agent.handle_eucalyptus_callback(qty_g)
        label = f"{qty_g}г добавлено" if qty_g else "Не добавлять"
        await callback.message.edit_text(f"✅ {label}")


def register_add_handlers(flower_stock_agent, db_factory) -> None:
    from app.bot.add_stock_fsm import register_add_stock_handlers
    register_add_stock_handlers(florist_router, db_factory, flower_stock_agent)


def register_write_off_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.write_off_fsm import register_write_off_handlers
    register_write_off_handlers(florist_router, db_factory, flower_stock_agent)


def register_query_handlers(db_factory) -> None:
    from app.bot.stock_queries import register_stock_query_handlers
    register_stock_query_handlers(florist_router, db_factory)


def register_cancel_handler() -> None:
    from aiogram.fsm.context import FSMContext

    @florist_router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        current = await state.get_state()
        if current is not None:
            await state.clear()
            await message.answer("Отменено.")


def create_florist_bot(storage=None) -> tuple[Bot, Dispatcher] | None:
    if not settings.florist_bot_token:
        return None
    from aiogram.fsm.storage.memory import MemoryStorage
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp.include_router(florist_router)
    return bot, dp
