"""待解析身份队列（T066）。

- 无法解析的外部身份进入可见的待处理队列（unresolved 计数 + 建议）；
- 模糊匹配（名称/IP 相似）只产生待确认建议，未经人工或权威确认不合并；
- 身份补齐后重算：把 unresolved 事件重新关联到资源并触发新诊断修订。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Event, Resource, ResourceAlias


def build_suggestions(
    *, incoming: dict, known_resources: list[dict]
) -> list[dict]:
    """为未解析外部身份产生待确认建议（只建议，不合并）。

    匹配提示：显示名相同、或 external_id 中包含资源显示名。
    """
    external_id = incoming.get("external_id", "")
    namespace = incoming.get("namespace", "")
    suggestions: list[dict] = []
    for resource in known_resources:
        name = resource.get("display_name") or ""
        if not name:
            continue
        reason = None
        if name and name in external_id:
            reason = f"external_id 包含显示名 {name}"
        elif namespace.startswith("otel.") and resource.get("kind") == "service_instance" and name:
            # otel 实例常见形态 name@host：前缀相同视为提示
            prefix = external_id.split("@")[0]
            if prefix and prefix == name:
                reason = f"otel 实例前缀与显示名一致 {name}"
        if reason:
            suggestions.append(
                {
                    "resource_id": resource["id"],
                    "display_name": name,
                    "reason": reason,
                    "confirmed": False,
                }
            )
    return suggestions


async def record_unresolved(session: AsyncSession, event: Event) -> None:
    """未解析事件保持 resource_id=NULL + unresolved 状态（队列 = 可查询）。"""
    event.resolution_state = "unresolved"


async def count_unresolved(session: AsyncSession, project_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(Event.id)).where(
                Event.project_id == project_id,
                Event.resolution_state == "unresolved",
            )
        )
    ).scalar_one()


async def pending_identities(
    session: AsyncSession, project_id: uuid.UUID, *, limit: int = 100
) -> list[dict]:
    """待解析的外部身份列表（含建议）。"""
    rows = (
        await session.execute(
            select(Event.resource_ref, func.count(Event.id))
            .where(
                Event.project_id == project_id,
                Event.resolution_state == "unresolved",
                Event.resource_id.is_(None),
            )
            .group_by(Event.resource_ref)
            .limit(limit)
        )
    ).all()
    resources = (
        (
            await session.execute(
                select(Resource.id, Resource.display_name, Resource.kind).where(
                    Resource.project_id == project_id
                )
            )
        )
        .mappings()
        .all()
    )
    known = [dict(r) for r in resources]
    out = []
    for ref, count in rows:
        ref_dict = ref if isinstance(ref, dict) else {}
        suggestions = build_suggestions(incoming=ref_dict, known_resources=known)
        out.append(
            {
                "namespace": ref_dict.get("namespace"),
                "external_id": ref_dict.get("external_id"),
                "event_count": count,
                "suggestions": suggestions,
            }
        )
    return out


async def reassociate_unresolved(
    session: AsyncSession,
    *,
    namespace: str,
    external_id: str,
    resource_id: uuid.UUID,
) -> int:
    """身份补齐后：把 unresolved 事件重新关联到资源。

    返回重算的事件数；调用方随后应触发诊断重算（新修订）。
    """
    now = datetime.now(UTC)
    # 确认别名存在（幂等）
    exists = (
        await session.execute(
            select(ResourceAlias.id).where(
                ResourceAlias.namespace == namespace,
                ResourceAlias.external_id == external_id,
                ResourceAlias.valid_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if exists is None:
        session.add(
            ResourceAlias(
                resource_id=resource_id,
                namespace=namespace,
                external_id=external_id,
                valid_from=now,
                provenance="observed",
                confirmed_by="identity-reassociation",
            )
        )
        await session.flush()

    result = await session.execute(
        update(Event)
        .where(
            Event.resource_ref["namespace"].astext == namespace,
            Event.resource_ref["external_id"].astext == external_id,
            Event.resolution_state == "unresolved",
            Event.resource_id.is_(None),
        )
        .values(resource_id=resource_id, resolution_state="resolved")
    )
    await session.flush()
    return result.rowcount or 0
