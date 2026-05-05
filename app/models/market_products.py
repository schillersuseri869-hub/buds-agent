import uuid
from decimal import Decimal
from typing import Optional
from sqlalchemy import String, Numeric, Enum, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base


class MarketProduct(Base):
    __tablename__ = "market_products"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    market_sku: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    catalog_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    crossed_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    min_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    optimal_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_pr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    storefront_price: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("active", "hidden", name="product_status"), nullable=False, default="active"
    )
