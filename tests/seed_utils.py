"""集成测试共享种子工具：直接以 ORM 构造资源、别名与关系。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Relationship, Resource, ResourceAlias

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")


async def seed_resource(
    session: AsyncSession,
    *,
    kind: str,
    provider: str,
    source_id: str,
    namespace: str,
    external_id: str,
    display_name: str | None = None,
    provenance: str = "observed",
    provider_instance: str = "default",
    now: datetime | None = None,
) -> Resource:
    now = now or datetime.now(UTC)
    resource = Resource(
        tenant_id=TENANT,
        project_id=PROJECT,
        kind=kind,
        provider=provider,
        provider_instance=provider_instance,
        source_id=source_id,
        display_name=display_name,
        provenance=provenance,
        first_seen=now,
        last_seen=now,
    )
    session.add(resource)
    await session.flush()
    session.add(
        ResourceAlias(
            resource_id=resource.id,
            namespace=namespace,
            external_id=external_id,
            valid_from=now,
            provenance=provenance,
        )
    )
    await session.flush()
    return resource


async def seed_relationship(
    session: AsyncSession,
    *,
    from_resource: Resource,
    to_resource: Resource,
    type_: str,
    source: str = "test-seed",
    provenance: str = "observed",
    valid_from: datetime | None = None,
) -> Relationship:
    rel = Relationship(
        tenant_id=TENANT,
        project_id=PROJECT,
        from_id=from_resource.id,
        type=type_,
        to_id=to_resource.id,
        valid_from=valid_from or datetime.now(UTC),
        source=source,
        provenance=provenance,
    )
    session.add(rel)
    await session.flush()
    return rel


async def seed_full_stack(
    session: AsyncSession,
    *,
    prefix: str = "us1",
    provenance: str = "observed",
    now: datetime | None = None,
) -> dict[str, Resource]:
    """种子：项目 / 宿主机 / VM / 容器 / 服务实例 及其关系。

    now 用于 first_seen / valid_from；默认取当前时间。测试使用固定历史事件
    时间时必须显式传入早于事件的 now，避免关系在诊断窗口外。
    """
    now = now or datetime.now(UTC)
    project = await seed_resource(
        session, kind="project", provider="manual", source_id=f"{prefix}-project",
        namespace="layertrace.project", external_id=f"{prefix}-project",
        display_name=f"{prefix} 项目", provenance="manual",
    )
    host = await seed_resource(
        session, kind="host", provider="zsvirt", source_id=f"{prefix}-host-1",
        namespace="zsvirt.host", external_id=f"{prefix}-host-1",
        display_name="host-1", provenance=provenance, now=now,
    )
    vm = await seed_resource(
        session, kind="vm", provider="zsvirt", source_id=f"{prefix}-vm-1",
        namespace="zsvirt.vm", external_id=f"{prefix}-vm-1",
        display_name="vm-1", provenance=provenance, now=now,
    )
    container = await seed_resource(
        session, kind="container", provider="docker", source_id=f"{prefix}-container-{prefix}",
        namespace="docker.container", external_id=f"{prefix}c0" * 8,
        display_name="demo-agent", provenance=provenance, now=now,
    )
    service_instance = await seed_resource(
        session, kind="service_instance", provider="otel", source_id=f"{prefix}-si-1",
        namespace="otel.service_instance", external_id=f"{prefix}-si-1",
        display_name="demo-agent-instance", provenance=provenance, now=now,
    )
    await seed_relationship(session, from_resource=host, to_resource=vm, type_="hosts", provenance=provenance, valid_from=now)
    await seed_relationship(session, from_resource=vm, to_resource=container, type_="contains", provenance=provenance, valid_from=now)
    await seed_relationship(
        session, from_resource=container, to_resource=service_instance, type_="runs", provenance=provenance, valid_from=now
    )
    for node in (host, vm, container, service_instance):
        await seed_relationship(
            session, from_resource=node, to_resource=project, type_="belongs_to", provenance="manual", valid_from=now
        )
    await session.commit()
    return {
        "project": project, "host": host, "vm": vm,
        "container": container, "service_instance": service_instance,
        "refs": {
            "project": f"{prefix}-project",
            "host": f"{prefix}-host-1",
            "vm": f"{prefix}-vm-1",
            "container": f"{prefix}c0" * 8,
            "service_instance": f"{prefix}-si-1",
        },
    }
