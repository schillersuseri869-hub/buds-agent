import uuid
from decimal import Decimal
from datetime import date
from sqlalchemy import String, Numeric, Date, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class RawMaterial(Base):
    __tablename__ = "raw_materials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(
        Enum("flower", "consumable", name="raw_material_type"), nullable=False
    )
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    physical_stock: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    reserved: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    cost_per_unit: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    last_delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    @property
    def available(self) -> Decimal:
        return self.physical_stock - self.reserved - Decimal("2")
