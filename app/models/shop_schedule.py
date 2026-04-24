import uuid
from datetime import datetime
from sqlalchemy import JSON, DateTime, Enum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class ShopSchedule(Base):
    __tablename__ = "shop_schedule"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    standard_schedule: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    override_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_action_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    scheduled_action: Mapped[str | None] = mapped_column(
        Enum("open", "close", name="schedule_action"), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
