"""数据源状态与增量游标模型（data-model.md 2.12 / 2.13）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import Base, UpdatedAtMixin, UUIDPrimaryKey

SOURCE_STATES = (
    "not_configured",
    "healthy",
    "degraded",
    "stale",
    "unavailable",
    "permission_denied",
)


class SourceStatus(UUIDPrimaryKey, UpdatedAtMixin, Base):
    """PostgreSQL、Prometheus、ZSvirt、探针、Tempo 等数据源健康与能力。"""

    __tablename__ = "source_statuses"
    __table_args__ = (
        UniqueConstraint("source_type", "source_instance", name="uq_source_status_identity"),
        CheckConstraint(
            "state IN ('not_configured','healthy','degraded','stale','unavailable','permission_denied')",
            name="ck_source_status_state",
        ),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0", name="ck_source_status_latency"),
        CheckConstraint("unresolved_count >= 0", name="ck_source_status_unresolved"),
        CheckConstraint("dropped_count >= 0", name="ck_source_status_dropped"),
    )

    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_instance: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    missing_permissions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    unresolved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queue_lag_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    dropped_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # [{start, end}] 数据空档
    data_gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)


class IngestionCursor(UUIDPrimaryKey, Base):
    """增量提供方游标；批次与游标必须同事务提交，失败不得前移。"""

    __tablename__ = "ingestion_cursors"
    __table_args__ = (
        UniqueConstraint("provider_instance", "stream", name="uq_cursor_stream"),
        Index("ix_cursor_committed", "provider_instance", "committed_at"),
    )

    provider_instance: Mapped[str] = mapped_column(String(128), nullable=False)
    stream: Mapped[str] = mapped_column(String(128), nullable=False)
    cursor: Mapped[str] = mapped_column(Text, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
