from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from app.config import settings

florist_router = Router()


@florist_router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer("Флорист подключён. Ожидаю заказы.")


def create_florist_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=settings.florist_bot_token)
    dp = Dispatcher()
    dp.include_router(florist_router)
    return bot, dp
