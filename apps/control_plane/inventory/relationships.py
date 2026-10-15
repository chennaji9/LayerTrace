"""生命周期关系维护（T065）。

容器消失/重建、VM 重注册/迁移时：关闭旧关系并建立新关系，
历史对象与历史任务保持原引用。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Relationship, Resource

TENANT_PLACEHOLDER = None  # 由调用方传入


async def close_and_rebuild(
    session: AsyncSession,
    *,
    vm_id: uuid.UUID,
    old_container: Resource,
    new_container: Resource,
    old_service_instance: Resource | None,
    new_service_instance: Resource | None,
    at: datetime,
    source: str = "runtime",
) -> None:
    """容器重建：关闭旧 vm contains container 与 container runs service_instance，
    建立新关系；旧关系保留历史（valid_to=at）。"""
    tenant_id = old_container.tenant_id
    project_id = old_container.project_id

    # 关闭旧 contains
    old_contains = (
        await session.execute(
            select(Relationship).where(
                Relationship.from_id == vm_id,
                Relationship.type == "contains",
                Relationship.to_id == old_container.id,
                Relationship.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if old_contains is not None:
        old_contains.valid_to = at

    # 建立新 contains
    session.add(
        Relationship(
            tenant_id=tenant_id,
            project_id=project_id,
            from_id=vm_id,
            type="contains",
            to_id=new_container.id,
            valid_from=at,
            source=source,
            provenance="observed",
        )
    )

    if old_service_instance is not None and new_service_instance is not None:
        old_runs = (
            await session.execute(
                select(Relationship).where(
                    Relationship.from_id == old_container.id,
                    Relationship.type == "runs",
                    Relationship.to_id == old_service_instance.id,
                    Relationship.valid_to.is_(None),
                )
            )
        ).scalar_one_or_none()
        if old_runs is not None:
            old_runs.valid_to = at
        session.add(
            Relationship(
                tenant_id=tenant_id,
                project_id=project_id,
                from_id=new_container.id,
                type="runs",
                to_id=new_service_instance.id,
                valid_from=at,
                source=source,
                provenance="observed",
            )
        )

    # 旧容器资源做生命周期关闭（软删除）
    old_container.deleted_at = at
    await session.flush()


async def handle_vm_reregister(
    session: AsyncSession,
    *,
    old_vm: Resource,
    new_vm: Resource,
    host_id: uuid.UUID | None,
    at: datetime,
    source: str = "zsvirt",
) -> None:
    """VM 重注册 / 迁移：关闭旧 host hosts vm，建立新关系。"""
    if host_id is None:
        return
    old_hosts = (
        await session.execute(
            select(Relationship).where(
                Relationship.from_id == host_id,
                Relationship.type == "hosts",
                Relationship.to_id == old_vm.id,
                Relationship.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if old_hosts is not None:
        old_hosts.valid_to = at
    session.add(
        Relationship(
            tenant_id=old_vm.tenant_id,
            project_id=old_vm.project_id,
            from_id=host_id,
            type="hosts",
            to_id=new_vm.id,
            valid_from=at,
            source=source,
            provenance="observed",
        )
    )
    await session.flush()
