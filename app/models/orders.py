import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import String, Numeric, Enum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    market_order_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum(
            "waiting", "ready", "shipped", "delivered", "cancelled", "timed_out",
            name="order_status",
        ),
        nullable=False,
        default="waiting",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    timer_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sale_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    estimated_commission_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    actual_services_cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_payout: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    actual_discount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False
    )
    quantity: Mapped[int]
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
