import uuid
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)


def _parse_decimal(text: str) -> Decimal:
    d = Decimal(text.replace(",", "."))
    if d <= 0:
        raise ValueError("must be positive")
    return d


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


class AddStockStates(StatesGroup):
    SelectMaterial = State()
    EnterQuantity = State()
    EnterPrice = State()


async def _build_materials_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
        materials = list(result.scalars().all())
    buttons = [
        [InlineKeyboardButton(
            text=f"{m.name} ({_fmt(m.physical_stock)} {m.unit})",
            callback_data=f"add_mat:{m.id}",
        )]
        for m in materials
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _make_price_handler(db_factory: async_sessionmaker, flower_stock_agent):
    async def handle_price(message: Message, state: FSMContext):
        try:
            price = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 80 или 45.50")
            return
        data = await state.get_data()
        material_id = uuid.UUID(data["material_id"])
        qty = Decimal(data["quantity"])
        async with db_factory() as db:
            mat = await stock_ops.record_arrival(db, material_id, qty, price)
        await state.clear()
        await flower_stock_agent._update_storefront()
        await flower_stock_agent.sync_to_grist(mat)
        await message.answer(
            f"✅ Приход: {_fmt(qty)} {mat.unit} «{mat.name}» по {_fmt(price)}₽\n"
            f"Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
        )
    return handle_price


def register_add_stock_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    @router.message(Command("add"))
    async def cmd_add(message: Message, state: FSMContext):
        keyboard = await _build_materials_keyboard(db_factory)
        await message.answer("Выберите материал:", reply_markup=keyboard)
        await state.set_state(AddStockStates.SelectMaterial)

    @router.callback_query(
        AddStockStates.SelectMaterial,
        lambda c: c.data and c.data.startswith("add_mat:"),
    )
    async def handle_material_selected(callback: CallbackQuery, state: FSMContext):
        material_id_str = callback.data.split(":", 1)[1]
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == uuid.UUID(material_id_str))
            )
            material = result.scalar_one_or_none()
        if material is None:
            await callback.answer("Материал не найден.")
            return
        await state.update_data(
            material_id=str(material.id),
            material_name=material.name,
            material_unit=material.unit,
        )
        await callback.message.edit_text(
            f"«{material.name}» выбран. Сколько {material.unit}?"
        )
        await state.set_state(AddStockStates.EnterQuantity)
        await callback.answer()

    @router.message(AddStockStates.EnterQuantity, ~F.text.startswith("/"))
    async def handle_quantity(message: Message, state: FSMContext):
        try:
            qty = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 50 или 10.5")
            return
        await state.update_data(quantity=str(qty))
        await message.answer("Цена за единицу (₽)?")
        await state.set_state(AddStockStates.EnterPrice)

    router.message(AddStockStates.EnterPrice, ~F.text.startswith("/"))(
        _make_price_handler(db_factory, flower_stock_agent)
    )
