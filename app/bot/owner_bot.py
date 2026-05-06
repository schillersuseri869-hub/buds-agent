from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import default_state
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
    @owner_router.message(Command("stock"))
    async def handle_stock_command(message: Message):
        report = await flower_stock_agent.get_stock_report()
        await message.answer(report)

    @owner_router.message(StateFilter(default_state))
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


def register_sync_handler(flower_stock_agent, db_factory) -> None:
    from app.agents.flower_stock.sheets_loader import load_from_grist, push_materials_status_to_grist

    @owner_router.message(Command("sync"))
    async def cmd_sync(message: Message):
        await message.answer("🔄 Синхронизирую с Grist...")
        try:
            async with db_factory() as db:
                n_mat, n_prod = await load_from_grist(
                    db,
                    settings.grist_url,
                    settings.grist_doc_id,
                    settings.grist_api_key,
                )
            await flower_stock_agent._update_storefront()
            async with db_factory() as db:
                await push_materials_status_to_grist(
                    settings.grist_url, settings.grist_doc_id, settings.grist_api_key, db
                )
            await message.answer(f"✅ Grist синхронизирован: {n_mat} материалов, {n_prod} товаров.")
        except Exception as exc:
            await message.answer(f"❌ Ошибка синхронизации: {exc}")


def register_add_handlers(flower_stock_agent, db_factory) -> None:
    from app.bot.add_stock_fsm import register_add_stock_handlers
    register_add_stock_handlers(owner_router, db_factory, flower_stock_agent)


def register_write_off_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.write_off_fsm import register_write_off_handlers
    register_write_off_handlers(owner_router, db_factory, flower_stock_agent)


def register_inventory_handler(flower_stock_agent, db_factory) -> None:
    from app.bot.inventory_fsm import register_inventory_handlers
    register_inventory_handlers(owner_router, db_factory, flower_stock_agent)


def register_query_handlers(db_factory) -> None:
    from app.bot.stock_queries import register_stock_query_handlers
    register_stock_query_handlers(owner_router, db_factory)


def register_cancel_handler() -> None:
    from aiogram.fsm.context import FSMContext

    @owner_router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext):
        current = await state.get_state()
        if current is not None:
            await state.clear()
            await message.answer("Отменено.")


def create_owner_bot(storage=None) -> tuple[Bot, Dispatcher]:
    from aiogram.fsm.storage.memory import MemoryStorage
    bot = Bot(token=settings.owner_bot_token)
    dp = Dispatcher(storage=storage or MemoryStorage())
    dp.include_router(owner_router)
    return bot, dp
