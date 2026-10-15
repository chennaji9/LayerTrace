"""资源与资源别名模型（data-model.md 2.1 / 2.2）。"""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.control_plane.models.base import (
    PROVENANCE_CHECK,
    Base,
    UUIDPrimaryKey,
)

RESOURCE_KINDS = (
    "project",
    "host",
    "vm",
    "container",
    "service",
    "service_instance",
    "gpu",
    "vgpu",
)


class Resource(UUIDPrimaryKey, Base):
    __tablename__ = "resources"
    __table_args__ = (
        # 唯一键严格为 (tenant_id, provider, provider_instance, kind, source_id)；
        # display_name 不参与去重。
        UniqueConstraint(
            "tenant_id",
            "provider",
            "provider_instance",
            "kind",
            "source_id",
            name="uq_resource_identity",
        ),
        CheckConstraint(
            "kind IN ('project','host','vm','container','service','service_instance','gpu','vgpu')",
            name="ck_resource_kind",
        ),
        CheckConstraint(PROVENANCE_CHECK, name="ck_resource_provenance"),
        CheckConstraint("first_seen <= last_seen", name="ck_resource_seen_order"),
        Index("ix_resource_project_kind", "project_id", "kind"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_instance: Mapped[str] = mapped_column(String(128), nullable=False)
    source_id: Mapped[str] = mapped_column(String(512), nullable=False)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 仅在完整对账确认消失后设置（软删除）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResourceAlias(UUIDPrimaryKey, Base):
    __tablename__ = "resource_aliases"
    __table_args__ = (
        # 同一命名空间和外部 ID 在同一时间只能指向一个资源（当前有效唯一）
        Index(
            "uq_alias_active",
            "namespace",
            "external_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
        ),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="ck_alias_validity"),
        CheckConstraint(PROVENANCE_CHECK, name="ck_alias_provenance"),
        Index("ix_alias_resource", "resource_id"),
    )

    resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("resources.id", ondelete="CASCADE"), nullable=False
    )
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    # 完整外部 ID，禁止截断容器 ID
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False)
    confirmed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
