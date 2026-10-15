"""诊断修订模型（data-model.md 2.11）。

不可变：迟到数据触发新 revision，禁止覆盖历史结论。
支持/反证/缺失数组字段必填（NOT NULL 默认空），不得缺失。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import Base, CreatedAtMixin, UUIDPrimaryKey

SUPPORT_LEVELS = ("high", "medium", "low")
TIME_CONFIDENCE_VALUES = ("trusted", "degraded", "unknown")


class Diagnosis(UUIDPrimaryKey, CreatedAtMixin, Base):
    __tablename__ = "diagnoses"
    __table_args__ = (
        # 同一事件单 revision 单调递增且唯一
        UniqueConstraint("incident_id", "revision", name="uq_diagnosis_revision"),
        CheckConstraint("revision >= 1", name="ck_diagnosis_revision"),
        CheckConstraint(
            "time_confidence IN ('trusted','degraded','unknown')",
            name="ck_diagnosis_time_confidence",
        ),
        CheckConstraint("window_start <= window_end", name="ck_diagnosis_window"),
        Index("ix_diagnosis_incident", "incident_id", "revision"),
        Index("ix_diagnosis_symptom", "symptom_resource_id", "created_at"),
    )

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    # 诊断入口资源
    symptom_resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="SET NULL"), nullable=True
    )
    # 实际使用的关联窗口
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 候选：[{rule_id,title,support_level,score,supporting,contradicting,missing,
    #        recommendations,verification}]；score 仅用于同级排序，不是概率
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    supporting_evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    contradicting_evidence_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    missing_evidence: Mapped[list[str]] = mapped_column(
        ARRAY(String(256)), nullable=False, default=list
    )
    # 观测影响与潜在影响分离
    observed_impact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    potential_impact: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # 每个 evaluator 的精确版本
    rule_versions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    time_confidence: Mapped[str] = mapped_column(String(16), nullable=False)
    # 图遍历超限时为真
    context_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
