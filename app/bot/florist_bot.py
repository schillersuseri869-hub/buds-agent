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


def create_florist_bot() -> tuple[Bot, Dispatcher] | None:
    if not settings.florist_bot_token:
        return None
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher()
    dp.include_router(florist_router)
    return bot, dp
