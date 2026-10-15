"""受控故障 / 回放剧本执行记录（data-model.md 2.14）。

状态机：
    pending -> running -> verifying -> succeeded
                        -> restoring -> restored
    任意活动状态 -> failed -> restoring -> restored/restore_failed

真实注入只允许 MODE=lab、带测试标签且位于授权项目的目标（授权服务强制）。
到达 deadline_at 必须自动恢复（执行器强制）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import PROVENANCE_CHECK, Base, UUIDPrimaryKey

SCENARIO_STATES = (
    "pending",
    "running",
    "verifying",
    "succeeded",
    "failed",
    "restoring",
    "restored",
    "restore_failed",
)

ACTIVE_SCENARIO_STATES = ("pending", "running", "verifying")

# 允许的状态转换表（服务层强制）
SCENARIO_TRANSITIONS = {
    "pending": {"running", "failed"},
    "running": {"verifying", "failed", "restoring"},
    "verifying": {"succeeded", "failed", "restoring"},
    # succeeded/failed 后仍可显式恢复（quickstart：run -> verify -> restore）
    "succeeded": {"restoring"},
    "failed": {"restoring"},
    "restoring": {"restored", "restore_failed"},
    "restored": set(),
    "restore_failed": set(),
}


class ScenarioRun(UUIDPrimaryKey, Base):
    __tablename__ = "scenario_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('replay','lab')", name="ck_scenario_mode"),
        CheckConstraint(
            "state IN ('pending','running','verifying','succeeded','failed','restoring','restored','restore_failed')",
            name="ck_scenario_state",
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_scenario_provenance"),
        CheckConstraint("deadline_at IS NULL OR deadline_at > started_at", name="ck_scenario_deadline"),
        Index("ix_scenario_name_state", "scenario_name", "state", "started_at"),
    )

    scenario_name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 注入目标引用（必须带测试标签）
    target_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    started_by: Mapped[str] = mapped_column(String(256), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 注入前快照，用于恢复
    original_configuration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    verification_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    restore_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
