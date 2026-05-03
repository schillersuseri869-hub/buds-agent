import uuid
import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.raw_materials import RawMaterial
from app.agents.flower_stock import stock_ops

logger = logging.getLogger(__name__)


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


class InventoryStates(StatesGroup):
    AuditMaterial = State()


async def _ask_next(message: Message, state: FSMContext, db_factory: async_sessionmaker):
    """Advance to the next material or finish the audit."""
    data = await state.get_data()
    material_ids: list[str] = data["material_ids"]
    index: int = data["index"]

    while index < len(material_ids):
        mid = uuid.UUID(material_ids[index])
        async with db_factory() as db:
            result = await db.execute(
                select(RawMaterial).where(RawMaterial.id == mid)
            )
            material = result.scalar_one_or_none()

        if material is None:
            index += 1
            await state.update_data(index=index)
            continue

        if material.reserved > 0:
            await message.answer(
                f"⚠️ «{material.name}» — в резерве {_fmt(material.reserved)} {material.unit}, пропускаю."
            )
            index += 1
            await state.update_data(index=index)
            continue

        await message.answer(
            f"📦 {material.name}: в системе {_fmt(material.physical_stock)} {material.unit}.\n"
            f"Сколько по факту? (или /skip чтобы пропустить)"
        )
        return

    corrections = data.get("corrections", 0)
    await state.clear()
    await message.answer(
        f"✅ Инвентаризация завершена. Исправлено {corrections} позиций."
    )


def register_inventory_handlers(
    router: Router, db_factory: async_sessionmaker, flower_stock_agent
) -> None:
    @router.message(Command("inventory"))
    async def cmd_inventory(message: Message, state: FSMContext):
        async with db_factory() as db:
            result = await db.execute(select(RawMaterial).order_by(RawMaterial.name))
            materials = list(result.scalars().all())
        if not materials:
            await message.answer("Склад пуст.")
            return
        await state.set_data({
            "material_ids": [str(m.id) for m in materials],
            "index": 0,
            "corrections": 0,
        })
        await state.set_state(InventoryStates.AuditMaterial)
        await _ask_next(message, state, db_factory)

    @router.message(InventoryStates.AuditMaterial, Command("skip"))
    async def handle_skip(message: Message, state: FSMContext):
        data = await state.get_data()
        await state.update_data(index=data["index"] + 1)
        await _ask_next(message, state, db_factory)

    @router.message(InventoryStates.AuditMaterial, ~F.text.startswith("/"))
    async def handle_count(message: Message, state: FSMContext):
        try:
            actual = Decimal((message.text or "").replace(",", "."))
            if actual < 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            await message.answer("Введите число ≥ 0, например: 47 или 10.5")
            return
        data = await state.get_data()
        mid = uuid.UUID(data["material_ids"][data["index"]])
        async with db_factory() as db:
            mat, delta = await stock_ops.record_inventory_correction(db, mid, actual)
        corrections = data.get("corrections", 0)
        if delta != Decimal("0"):
            sign = "+" if delta > 0 else ""
            await message.answer(f"✏️ {mat.name}: {sign}{_fmt(delta)} {mat.unit}")
            corrections += 1
        await state.update_data(index=data["index"] + 1, corrections=corrections)
        await _ask_next(message, state, db_factory)
        await flower_stock_agent._update_storefront()
        await flower_stock_agent.sync_to_grist(mat)
