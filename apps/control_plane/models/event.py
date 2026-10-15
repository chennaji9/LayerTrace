"""事实事件与证据模型（data-model.md 2.4 / 2.5）。"""

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


class Event(UUIDPrimaryKey, Base):
    """不可变的结构化事实；更新只能通过追加纠正事件，禁止覆盖。"""

    __tablename__ = "events"
    __table_args__ = (
        # 数据源提供的幂等键，全局唯一
        UniqueConstraint("event_id", name="uq_event_event_id"),
        CheckConstraint(
            "severity IN ('info','warning','error','critical')", name="ck_event_severity"
        ),
        CheckConstraint(
            "resolution_state IN ('resolved','unresolved','rejected')",
            name="ck_event_resolution_state",
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_event_provenance"),
        # replayed 必填 scenario 名称与偏移
        CheckConstraint(
            "provenance <> 'replayed' OR (scenario_name IS NOT NULL AND scenario_offset IS NOT NULL)",
            name="ck_event_replayed_scenario",
        ),
        Index("ix_event_resource_time", "tenant_id", "resource_id", "occurred_at"),
        Index(
            "ix_event_resolution", "project_id", "resolution_state", "observed_at"
        ),
        Index("ix_event_type_time", "project_id", "type", "occurred_at"),
    )

    # 数据源幂等键
    event_id: Mapped[str] = mapped_column(String(256), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 解析成功后设置；失败时保留空值并进入 unresolved 队列
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    # 原始命名空间与外部 ID，必须保留
    resource_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    # 经允许列表和脱敏后的属性
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 可含 task_id、trace_id，不得填造未知关联
    correlation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    # 原始证据引用，不保存禁止内容
    raw_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    resolution_state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="unresolved"
    )
    scenario_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scenario_offset: Mapped[str | None] = mapped_column(String(16), nullable=True)


class Evidence(UUIDPrimaryKey, CreatedAtMixin, Base):
    """诊断可长期引用的证据摘要与查询参数，不复制全部原始指标或日志。"""

    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(PROVENANCE_CHECK, name="ck_evidence_provenance"),
        CheckConstraint("range_start <= range_end", name="ck_evidence_range"),
        Index("ix_evidence_resource", "resource_id", "range_start", "range_end"),
        Index("ix_evidence_kind", "project_id", "kind"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 稳定来源引用（如 PromQL 查询指纹、事件 ID、日志位置）
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    query_parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 原始后端数据过期后仍保留摘要
    source_expired: Mapped[bool] = mapped_column(nullable=False, default=False)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
