"""库存服务：资源 upsert 与身份绑定。

- 资源唯一键 (tenant, provider, provider_instance, kind, source_id)；display_name
  只更新显示，不参与去重；
- 外部身份通过显式别名绑定（带有效期），名称/IP 不产生已确认别名；
- 重建/重注册场景由 relationships.py 处理关系关闭。

T064（US3）在此之上扩展软删除、别名确认与待解析队列。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Relationship, Resource, ResourceAlias
from apps.control_plane.models.relationship import ALLOWED_RELATION_PAIRS


async def upsert_resource(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    kind: str,
    provider: str,
    provider_instance: str,
    source_id: str,
    provenance: str,
    display_name: str | None = None,
    attributes: dict | None = None,
    now: datetime | None = None,
    namespace: str | None = None,
    external_id: str | None = None,
) -> Resource:
    """按唯一键 upsert 资源；可选同时建立显式别名。"""
    now = now or datetime.now(UTC)
    resource = (
        await session.execute(
            select(Resource).where(
                Resource.tenant_id == tenant_id,
                Resource.provider == provider,
                Resource.provider_instance == provider_instance,
                Resource.kind == kind,
                Resource.source_id == source_id,
            )
        )
    ).scalar_one_or_none()
    if resource is None:
        resource = Resource(
            tenant_id=tenant_id,
            project_id=project_id,
            kind=kind,
            provider=provider,
            provider_instance=provider_instance,
            source_id=source_id,
            display_name=display_name,
            attributes=attributes or {},
            provenance=provenance,
            first_seen=now,
            last_seen=now,
        )
        session.add(resource)
        await session.flush()
    else:
        resource.last_seen = now
        if display_name is not None:
            resource.display_name = display_name
        if attributes:
            resource.attributes = {**resource.attributes, **attributes}
        await session.flush()

    if namespace and external_id:
        await bind_alias(
            session,
            resource_id=resource.id,
            namespace=namespace,
            external_id=external_id,
            valid_from=now,
            provenance=provenance,
        )
    return resource


async def bind_alias(
    session: AsyncSession,
    *,
    resource_id: uuid.UUID,
    namespace: str,
    external_id: str,
    valid_from: datetime,
    provenance: str,
    confirmed_by: str | None = None,
) -> ResourceAlias | None:
    """建立显式别名；同名外部身份若已绑定其他资源则关闭旧绑定后建立新绑定。"""
    existing = (
        await session.execute(
            select(ResourceAlias).where(
                ResourceAlias.namespace == namespace,
                ResourceAlias.external_id == external_id,
                ResourceAlias.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.resource_id == resource_id:
            return existing
        # 同一外部身份当前只能指向一个资源：关闭旧绑定
        existing.valid_to = valid_from
    alias = ResourceAlias(
        resource_id=resource_id,
        namespace=namespace,
        external_id=external_id,
        valid_from=valid_from,
        provenance=provenance,
        confirmed_by=confirmed_by,
    )
    session.add(alias)
    await session.flush()
    return alias


async def upsert_relationship(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    from_id: uuid.UUID,
    type_: str,
    to_id: uuid.UUID,
    valid_from: datetime,
    valid_to: datetime | None,
    source: str,
    provenance: str,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> Relationship | None:
    """打开或关闭时态关系。

    - 端点 kind 与关系类型的组合必须合法（8 类有向关系）；
    - 同三元组已有有效关系且来源一致：更新 valid_to；否则关闭旧的建立新的。
    """
    if from_kind and to_kind:
        if (from_kind, type_, to_kind) not in ALLOWED_RELATION_PAIRS:
            return None
    if from_id == to_id:
        return None

    active = (
        await session.execute(
            select(Relationship).where(
                Relationship.from_id == from_id,
                Relationship.type == type_,
                Relationship.to_id == to_id,
                Relationship.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()

    if valid_to is None:
        if active is not None:
            active.source = source
            return active
        rel = Relationship(
            tenant_id=tenant_id,
            project_id=project_id,
            from_id=from_id,
            type=type_,
            to_id=to_id,
            valid_from=valid_from,
            valid_to=None,
            source=source,
            provenance=provenance,
        )
        session.add(rel)
        await session.flush()
        return rel

    # 关闭关系
    if active is not None:
        active.valid_to = valid_to
        await session.flush()
        return active
    return None


async def apply_discovery_batch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    batch,
    now: datetime,
) -> dict:
    """把提供方 DiscoveryBatch 写入库存（资源 + 别名 + 关系）。"""
    id_by_source: dict[tuple[str, str], uuid.UUID] = {}
    kinds: dict[uuid.UUID, str] = {}
    for res in batch.resources:
        resource = await upsert_resource(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            kind=res.kind,
            provider=res.provider,
            provider_instance=res.provider_instance,
            source_id=res.source_id,
            provenance=res.provenance,
            display_name=res.display_name,
            attributes=res.attributes,
            now=now,
            namespace=res.namespace,
            external_id=res.external_id,
        )
        id_by_source[(res.provider_instance, res.source_id)] = resource.id
        kinds[resource.id] = res.kind

    for rel in batch.relationships:
        from_id = id_by_source.get(("replay", rel.from_source_id)) or id_by_source.get(
            ("default", rel.from_source_id)
        )
        to_id = id_by_source.get(("replay", rel.to_source_id)) or id_by_source.get(
            ("default", rel.to_source_id)
        )
        if from_id is None or to_id is None:
            continue
        await upsert_relationship(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            from_id=from_id,
            type_=rel.type,
            to_id=to_id,
            valid_from=rel.valid_from,
            valid_to=rel.valid_to,
            source=rel.source,
            provenance=rel.provenance,
            from_kind=kinds.get(from_id),
            to_kind=kinds.get(to_id),
        )
    return {"resources": len(id_by_source)}
