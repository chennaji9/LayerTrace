"""Agent 任务运行与资源快照模型（data-model.md 2.6 / 2.7）。"""

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

from apps.control_plane.models.base import PROVENANCE_CHECK, Base, UUIDPrimaryKey

TASK_STATUSES = ("running", "succeeded", "failed", "timed_out", "interrupted")
TERMINAL_TASK_STATUSES = ("succeeded", "failed", "timed_out", "interrupted")


class TaskRun(UUIDPrimaryKey, Base):
    __tablename__ = "task_runs"
    __table_args__ = (
        # task_id 项目内唯一
        UniqueConstraint("project_id", "task_id", name="uq_task_run_project_task"),
        CheckConstraint(
            "status IN ('running','succeeded','failed','timed_out','interrupted')",
            name="ck_task_run_status",
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_task_run_provenance"),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0", name="ck_task_run_duration"
        ),
        CheckConstraint(
            "model_duration_ms IS NULL OR model_duration_ms >= 0",
            name="ck_task_run_model_duration",
        ),
        CheckConstraint(
            "tool_duration_ms IS NULL OR tool_duration_ms >= 0",
            name="ck_task_run_tool_duration",
        ),
        CheckConstraint(
            "ended_at IS NULL OR ended_at >= started_at", name="ck_task_run_time_order"
        ),
        Index("ix_task_run_instance_time", "service_instance_id", "started_at"),
        Index("ix_task_run_status", "project_id", "status", "started_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # 由调用方和服务共同携带，项目内唯一
    task_id: Mapped[str] = mapped_column(String(256), nullable=False)
    # 指向故障时服务实例
    service_instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="RESTRICT"), nullable=False
    )
    # 不作为指标标签
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 必须在执行前写入或进入 spool
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    # 结构化错误类别，不保存敏感正文
    error_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # interrupted 可为 inferred
    status_provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    # 推断中断时引用实例退出事件
    trigger_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="SET NULL"), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ResourceSnapshot(UUIDPrimaryKey, Base):
    __tablename__ = "resource_snapshots"
    __table_args__ = (
        # 同资源、时刻和配置哈希去重
        UniqueConstraint(
            "resource_id", "captured_at", "config_hash", name="uq_snapshot_dedup"
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_snapshot_provenance"),
        Index("ix_snapshot_resource", "resource_id", "captured_at"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 故障时可追溯的 CPU/内存限制等
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    image_digest: Mapped[str | None] = mapped_column(String(256), nullable=True)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
