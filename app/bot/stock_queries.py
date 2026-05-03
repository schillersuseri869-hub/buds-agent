import uuid
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)

_MOVEMENT_LABELS = {
    "arrival": "Приход",
    "reserve": "Резерв",
    "debit": "Списание (заказ)",
    "release": "Снятие резерва",
    "spoilage": "Порча",
    "defect": "Брак",
    "extra_debit": "Доп. списание",
    "inventory_correction": "Инвентаризация",
    "return": "Возврат",
}

_REPORT_KEYBOARD = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="За сегодня", callback_data="report:today"),
        InlineKeyboardButton(text="За неделю", callback_data="report:week"),
        InlineKeyboardButton(text="За месяц", callback_data="report:month"),
    ]
])


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def register_stock_query_handlers(router: Router, db_factory: async_sessionmaker) -> None:
    @router.message(Command("history"))
    async def cmd_history(message: Message):
        async with db_factory() as db:
            result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
            materials = list(result.scalars().all())
        if not materials:
            await message.answer("Склад пуст.")
            return
        buttons = [
            [InlineKeyboardButton(text=m.name, callback_data=f"hist_mat:{m.id}")]
            for m in materials
        ]
        await message.answer(
            "Выберите материал:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )

    @router.callback_query(lambda c: c.data and c.data.startswith("hist_mat:"))
    async def handle_history_material(callback: CallbackQuery):
        material_id = uuid.UUID(callback.data.split(":", 1)[1])
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == material_id)
            )
            material = result.scalar_one_or_none()
            movements = await stock_ops.get_material_history(db, material_id)
        if material is None:
            await callback.answer("Материал не найден.")
            return
        if not movements:
            await callback.message.edit_text(f"«{material.name}» — нет движений.")
            await callback.answer()
            return
        lines = [f"📋 История: «{material.name}»\n"]
        for m in movements:
            ts = m.created_at.strftime("%d.%m %H:%M")
            label = _MOVEMENT_LABELS.get(m.type, m.type)
            lines.append(f"{ts} · {label} {_fmt(m.quantity)} {material.unit}")
        await callback.message.edit_text("\n".join(lines))
        await callback.answer()

    @router.message(Command("report"))
    async def cmd_report(message: Message):
        await message.answer("Выберите период:", reply_markup=_REPORT_KEYBOARD)

    @router.callback_query(lambda c: c.data and c.data.startswith("report:"))
    async def handle_report_period(callback: CallbackQuery):
        period = callback.data.split(":", 1)[1]
        now = datetime.now(timezone.utc)
        if period == "today":
            since = now.replace(hour=0, minute=0, second=0, microsecond=0)
            label = "сегодня"
        elif period == "week":
            since = now - timedelta(days=7)
            label = "7 дней"
        else:
            since = now - timedelta(days=30)
            label = "30 дней"
        async with db_factory() as db:
            report = await stock_ops.get_report(db, since)
        text = (
            f"📊 Отчёт за {label}\n\n"
            f"Закупки: {_fmt(report.arrivals_cost)}₽\n"
            f"Списания: {_fmt(report.write_offs_cost)}₽\n"
            f"Стоимость склада: {_fmt(report.current_stock_value)}₽"
        )
        await callback.message.edit_text(text)
        await callback.answer()
