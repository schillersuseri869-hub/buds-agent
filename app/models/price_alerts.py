import uuid
from datetime import datetime
from sqlalchemy import Enum, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PriceAlert(Base):
    __tablename__ = "price_alerts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("market_products.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        Enum(
            "below_min", "below_optimal", "catalog_mismatch", "quarantine_risk",
            name="alert_type",
        ),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("new", "acked", "resolved", name="alert_status"), nullable=False, default="new"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
