import uuid
from decimal import Decimal
from typing import Optional
from datetime import datetime
from sqlalchemy import String, Numeric, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class PromoParticipation(Base):
    __tablename__ = "promo_participations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    promo_id: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    promo_type: Mapped[str] = mapped_column(String(50), nullable=False)
    promo_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    discount_pct: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
