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


def create_owner_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher()
    dp.include_router(owner_router)
    return bot, dp
