"""身份解析规则（US3）。

- 唯一键 (tenant, provider, provider_instance, kind, source_id)；
- 跨源身份只能通过带命名空间的显式别名合并；
- 名称与 IP 只是发现提示，禁止据此合并。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Resource, ResourceAlias

IdentityKey = tuple


def identity_key(
    *, tenant_id: uuid.UUID, provider: str, provider_instance: str, kind: str, source_id: str
) -> IdentityKey:
    """资源唯一键（与数据库 uq_resource_identity 一致）。"""
    return (tenant_id, provider, provider_instance, kind, source_id)


def alias_key(namespace: str, external_id: str) -> tuple[str, str]:
    """外部身份键：命名空间 + 完整外部 ID。"""
    return (namespace, external_id)


def same_display_name_is_not_identity(name_a: str, name_b: str, id_a: str, id_b: str) -> bool:
    """同名但不同 source_id 是否应保持独立（用于守护测试与规则说明）。"""
    if id_a == id_b:
        return False
    return True


async def resolve_by_alias(
    session: AsyncSession, namespace: str, external_id: str, *, at: datetime | None = None
) -> Resource | None:
    """按当前有效显式别名解析资源；未解析返回 None（不模糊合并）。"""
    row = (
        await session.execute(
            select(ResourceAlias)
            .where(
                ResourceAlias.namespace == namespace,
                ResourceAlias.external_id == external_id,
                ResourceAlias.valid_to.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return (
        await session.execute(select(Resource).where(Resource.id == row.resource_id))
    ).scalar_one_or_none()


async def confirm_alias(
    session: AsyncSession,
    *,
    resource_id: uuid.UUID,
    namespace: str,
    external_id: str,
    confirmed_by: str,
    valid_from: datetime,
) -> ResourceAlias | None:
    """人工确认别名后绑定；模糊建议必须经此入口才能生效。"""
    return await _bind(session, resource_id, namespace, external_id, valid_from, confirmed_by=confirmed_by)


async def _bind(
    session: AsyncSession,
    resource_id: uuid.UUID,
    namespace: str,
    external_id: str,
    valid_from: datetime,
    confirmed_by: str | None = None,
) -> ResourceAlias | None:
    from apps.control_plane.inventory.service import bind_alias

    return await bind_alias(
        session,
        resource_id=resource_id,
        namespace=namespace,
        external_id=external_id,
        valid_from=valid_from,
        provenance="observed",
        confirmed_by=confirmed_by,
    )
