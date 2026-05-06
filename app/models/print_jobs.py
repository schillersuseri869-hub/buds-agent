import uuid
from datetime import datetime
from sqlalchemy import String, Enum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func
from app.models.base import Base


class PrintJob(Base):
    __tablename__ = "print_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "sent", "done", "failed", "cancelled", name="print_job_status"),
        nullable=False,
        default="pending",
    )
    label_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
