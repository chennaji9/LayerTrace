"""Consumer receipts are separate from immutable event facts."""

import uuid
from datetime import datetime

from sqlalchemy import UUID, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import Base


class EventReceipt(Base):
    __tablename__ = "event_receipts"
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
