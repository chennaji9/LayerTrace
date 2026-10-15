"""模型基类。

- 主键为不可复用的 UUIDv4（客户端生成，禁止业务复用）。
- 所有时间戳为带时区的 UTC。
- 公共枚举（provenance 等）在各自模型中通过 CHECK 约束固化。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PROVENANCE_VALUES = ("observed", "simulated", "replayed", "inferred", "manual")

# 生成形如 provenance IN ('observed','simulated',...) 的 CHECK 约束表达式
PROVENANCE_CHECK = "provenance IN ({})".format(",".join(f"'{v}'" for v in PROVENANCE_VALUES))


class Base(DeclarativeBase):
    metadata = MetaData(schema="public")


class UUIDPrimaryKey:
    """不可复用的 UUID 主键。"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UpdatedAtMixin:
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
