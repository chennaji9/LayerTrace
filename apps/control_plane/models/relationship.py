"""时态关系模型（data-model.md 2.3）。

首版允许的 8 类有向关系（from_kind type to_kind）：
  host hosts vm / vm contains container / container runs service_instance /
  service_instance instance_of service / service depends_on service /
  vm attached_to vgpu / vgpu backed_by gpu / resource belongs_to project

类型白名单在 CHECK 中固化；端点 kind 与类型的组合校验由库存服务
（apps/control_plane/inventory）在写入前执行。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    UUID,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import PROVENANCE_CHECK, Base, UUIDPrimaryKey

RELATIONSHIP_TYPES = (
    "hosts",
    "contains",
    "runs",
    "instance_of",
    "depends_on",
    "attached_to",
    "backed_by",
    "belongs_to",
)

# 端点 kind 与关系类型的合法组合（服务层校验用）
ALLOWED_RELATION_PAIRS = {
    ("host", "hosts", "vm"),
    ("vm", "contains", "container"),
    ("container", "runs", "service_instance"),
    ("service_instance", "instance_of", "service"),
    ("service", "depends_on", "service"),
    ("service_instance", "depends_on", "service_instance"),
    ("vm", "attached_to", "vgpu"),
    ("vgpu", "backed_by", "gpu"),
    ("project", "belongs_to", "project"),
    ("host", "belongs_to", "project"),
    ("vm", "belongs_to", "project"),
    ("container", "belongs_to", "project"),
    ("service", "belongs_to", "project"),
    ("service_instance", "belongs_to", "project"),
    ("gpu", "belongs_to", "project"),
    ("vgpu", "belongs_to", "project"),
}


class Relationship(UUIDPrimaryKey, Base):
    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint("from_id <> to_id", name="ck_relationship_no_self"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from", name="ck_relationship_validity"
        ),
        CheckConstraint(
            "type IN ('hosts','contains','runs','instance_of','depends_on','attached_to','backed_by','belongs_to')",
            name="ck_relationship_type",
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_relationship_provenance"),
        # 同一关系三元组有效区间不得重叠（btree_gist 排斥约束，迁移中创建）
        Index("ix_relationship_to", "to_id"),
        Index("ix_relationship_from", "from_id"),
        Index("ix_relationship_valid", "from_id", "type", "to_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    to_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(256), nullable=False)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    confidence_note: Mapped[str | None] = mapped_column(Text, nullable=True)
