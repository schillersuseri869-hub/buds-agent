import uuid
from decimal import Decimal
from datetime import date, datetime
from sqlalchemy import Numeric, Date, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class EconomicsReport(Base):
    __tablename__ = "economics_reports"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    report_source: Mapped[str] = mapped_column(
        Enum("api", "manual_upload", name="report_source"), nullable=False
    )
    services_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    payout: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    sales_commission: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    delivery_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    boost_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    buyer_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    loyalty_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
