import uuid
from decimal import Decimal
from datetime import datetime
from sqlalchemy import Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    catalog_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    storefront_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    optimal_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
