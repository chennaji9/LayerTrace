"""告警、事件单与静默模型（data-model.md 2.8 / 2.9 / 2.10）。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import (
    PROVENANCE_CHECK,
    Base,
    CreatedAtMixin,
    UUIDPrimaryKey,
)


class Alert(UUIDPrimaryKey, Base):
    """规则产生的告警事实，不等同于事件单或通知。"""

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("state IN ('pending','firing','resolved')", name="ck_alert_state"),
        CheckConstraint(
            "severity IN ('info','warning','error','critical')", name="ck_alert_severity"
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_alert_provenance"),
        # 相同来源告警幂等
        UniqueConstraint("source", "source_alert_id", name="uq_alert_source"),
        Index("ix_alert_fingerprint", "project_id", "fingerprint", "state"),
        Index("ix_alert_resource", "resource_id", "starts_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    source_alert_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    # 派生告警折叠引用
    parent_alert_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True
    )
    annotations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Incident(UUIDPrimaryKey, Base):
    """同一稳定故障对象的告警聚合；控制面的唯一运维状态。"""

    __tablename__ = "incidents"
    __table_args__ = (
        # 指纹项目内唯一
        UniqueConstraint("project_id", "fingerprint", name="uq_incident_fingerprint"),
        CheckConstraint("state IN ('open','acknowledged','resolved')", name="ck_incident_state"),
        CheckConstraint("severity IN ('P1','P2','P3')", name="ck_incident_severity"),
        CheckConstraint("alert_count >= 1", name="ck_incident_alert_count"),
        CheckConstraint("reopen_count >= 0", name="ck_incident_reopen_count"),
        CheckConstraint("first_seen <= last_seen", name="ck_incident_seen_order"),
        Index("ix_incident_state", "project_id", "state", "severity", "last_seen"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 项目 + 规则 + 稳定故障对象
    fingerprint: Mapped[str] = mapped_column(String(256), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    severity: Mapped[str] = mapped_column(String(4), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    acknowledged_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 原始告警与通知计数（降噪量化，FR-020）
    notification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)


class Silence(UUIDPrimaryKey, CreatedAtMixin, Base):
    """只影响通知的静默；不改变 Alert 或 Incident 状态。"""

    __tablename__ = "silences"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_silence_window"),
        Index("ix_silence_active", "project_id", "ends_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 仅允许事件单指纹或 rule_id/resource_id/severity 组合（服务层校验键集合）
    matcher: Mapped[dict] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
