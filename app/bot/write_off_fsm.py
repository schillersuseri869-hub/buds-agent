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

_TYPE_LABELS = {
    "defect": "брак",
    "spoilage": "порча",
    "extra_debit": "к заказу",
}

_TYPE_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="🪲 Брак", callback_data="wo_type:defect"),
        InlineKeyboardButton(text="🌿 Порча", callback_data="wo_type:spoilage"),
        InlineKeyboardButton(text="📦 К заказу", callback_data="wo_type:extra_debit"),
    ]
])


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _parse_decimal(text: str) -> Decimal:
    d = Decimal(text.replace(",", "."))
    if d <= 0:
        raise ValueError("must be positive")
    return d


class WriteOffStates(StatesGroup):
    SelectType = State()
    SelectMaterial = State()
    EnterQuantity = State()
    SelectOrder = State()


async def _build_materials_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
        materials = list(result.scalars().all())
    buttons = [
        [InlineKeyboardButton(
            text=f"{m.name} ({_fmt(m.physical_stock)} {m.unit})",
            callback_data=f"wo_mat:{m.id}",
        )]
        for m in materials
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_orders_keyboard(db_factory: async_sessionmaker) -> InlineKeyboardMarkup:
    async with db_factory() as db:
        orders = await stock_ops.get_recent_orders(db, limit=20)
    if not orders:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Нет заказов", callback_data="wo_order:none:none")]
        ])
    buttons = [
        [InlineKeyboardButton(
            text=f"#{o.market_order_id}",
            callback_data=f"wo_order:{o.id}:{o.market_order_id}",
        )]
        for o in orders
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _make_complete_handler(db_factory: async_sessionmaker, flower_stock_agent):
    async def complete_write_off(message: Message, state: FSMContext):
        data = await state.get_data()
        material_id = uuid.UUID(data["material_id"])
        qty = Decimal(data["quantity"])
        wo_type = data["wo_type"]

        async with db_factory() as db:
            if wo_type == "extra_debit":
                order_id = uuid.UUID(data["order_id"])
                market_order_id = data["market_order_id"]
                mat = await stock_ops.record_extra_debit(
                    db, material_id, order_id, qty,
                    note=f"доп. списание к заказу #{market_order_id}",
                )
                label = f"к заказу #{market_order_id}"
            else:
                mat = await stock_ops.record_write_off(db, material_id, qty, wo_type)
                label = _TYPE_LABELS[wo_type]

        await state.clear()
        await flower_stock_agent._update_storefront()
        await flower_stock_agent.sync_to_grist(mat)
        await message.answer(
            f"✅ Списано: {_fmt(qty)} {mat.unit} «{mat.name}» ({label})\n"
            f"Остаток: {_fmt(mat.physical_stock)} {mat.unit}."
        )
    return complete_write_off


def register_write_off_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    complete_handler = _make_complete_handler(db_factory, flower_stock_agent)

    @router.message(Command("write_off"))
    async def cmd_write_off(message: Message, state: FSMContext):
        await message.answer("Тип списания:", reply_markup=_TYPE_KEYBOARD)
        await state.set_state(WriteOffStates.SelectType)

    @router.callback_query(
        WriteOffStates.SelectType,
        lambda c: c.data and c.data.startswith("wo_type:"),
    )
    async def handle_type_selected(callback: CallbackQuery, state: FSMContext):
        wo_type = callback.data.split(":", 1)[1]
        await state.update_data(wo_type=wo_type)
        keyboard = await _build_materials_keyboard(db_factory)
        await callback.message.edit_text("Выберите материал:", reply_markup=keyboard)
        await state.set_state(WriteOffStates.SelectMaterial)
        await callback.answer()

    @router.callback_query(
        WriteOffStates.SelectMaterial,
        lambda c: c.data and c.data.startswith("wo_mat:"),
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
        await state.set_state(WriteOffStates.EnterQuantity)
        await callback.answer()

    @router.message(WriteOffStates.EnterQuantity, ~F.text.startswith("/"))
    async def handle_quantity(message: Message, state: FSMContext):
        try:
            qty = _parse_decimal(message.text or "")
        except (InvalidOperation, ValueError):
            await message.answer("Введите положительное число, например: 3 или 1.5")
            return
        data = await state.get_data()
        await state.update_data(quantity=str(qty))
        if data["wo_type"] == "extra_debit":
            keyboard = await _build_orders_keyboard(db_factory)
            await message.answer("Выберите заказ:", reply_markup=keyboard)
            await state.set_state(WriteOffStates.SelectOrder)
        else:
            await complete_handler(message, state)

    @router.callback_query(
        WriteOffStates.SelectOrder,
        lambda c: c.data and c.data.startswith("wo_order:"),
    )
    async def handle_order_selected(callback: CallbackQuery, state: FSMContext):
        parts = callback.data.split(":", 2)
        if parts[1] == "none":
            await callback.answer("Нет доступных заказов.")
            return
        await state.update_data(order_id=parts[1], market_order_id=parts[2])
        await callback.answer()
        await complete_handler(callback.message, state)
