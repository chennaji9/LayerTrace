"""事件仓储：仅追加、幂等、单批事务。

- 以 `event_id` 去重：库内已存在或批内重复的条目计入 duplicate，不重复写入。
- 身份解析失败的事件仍会落库（resource_id 为空、resolution_state=unresolved），
  并计入 unresolved；身份补齐后由库存服务重新关联。
- 属性写入前统一脱敏；被排除的键名记入 payload._redacted 供审计。
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.ingestion.redaction import redact_attributes
from apps.control_plane.models import Event, Resource, ResourceAlias
from packages.contracts.schemas import EventBatch, EventInput, IngestionResult


async def _resolve_aliases(
    session: AsyncSession, refs: set[tuple[str, str]], *, tenant_id=None, project_id=None, at=None
) -> dict[tuple[str, str], uuid.UUID]:
    """Resolve explicit identities within project scope at the fact's original time."""
    resolved: dict[tuple[str, str], uuid.UUID] = {}
    for namespace, external_id in refs:
        query = select(ResourceAlias.resource_id).join(
            Resource, Resource.id == ResourceAlias.resource_id
        )
        if tenant_id is not None:
            query = query.where(Resource.tenant_id == tenant_id)
        if project_id is not None:
            query = query.where(Resource.project_id == project_id)
        if at is not None:
            query = query.where(
                ResourceAlias.valid_from <= at,
                or_(ResourceAlias.valid_to.is_(None), ResourceAlias.valid_to > at),
            )
        else:
            query = query.where(ResourceAlias.valid_to.is_(None))
        result = await session.execute(
            query.where(
                ResourceAlias.namespace == namespace,
                ResourceAlias.external_id == external_id,
            ).limit(1)
        )
        row = result.first()
        if row is not None:
            resolved[(namespace, external_id)] = row[0]
    return resolved


async def ingest_batch(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    batch: EventBatch,
) -> IngestionResult:
    # 批内去重
    seen: dict[str, EventInput] = {}
    unique_events = []
    duplicate = 0
    for event in batch.events:
        if event.event_id in seen:
            duplicate += 1
            continue
        seen[event.event_id] = event
        unique_events.append(event)

    # 库内已存在的 event_id（幂等键）
    existing: set[str] = set()
    all_ids = [e.event_id for e in unique_events]
    for i in range(0, len(all_ids), 200):
        chunk = all_ids[i : i + 200]
        result = await session.execute(select(Event.event_id).where(Event.event_id.in_(chunk)))
        existing.update(row[0] for row in result.fetchall())
    duplicate += len(existing)

    # 身份解析（当前有效别名）
    unresolved = 0
    to_insert: list[Event] = []
    for event in unique_events:
        if event.event_id in existing:
            continue
        redaction = redact_attributes(event.attributes)
        ref_key = (event.resource_ref.namespace, event.resource_ref.external_id)
        alias_map = await _resolve_aliases(
            session, {ref_key}, tenant_id=tenant_id, project_id=project_id, at=event.occurred_at
        )
        resource_id = alias_map.get(ref_key)
        if resource_id is None:
            unresolved += 1

        payload = dict(redaction.attributes)
        if redaction.removed_keys or redaction.redacted_value_keys:
            payload["_redacted"] = {
                "removed_keys": sorted(redaction.removed_keys),
                "value_scrubbed_keys": sorted(redaction.redacted_value_keys),
            }

        to_insert.append(
            Event(
                event_id=event.event_id,
                tenant_id=tenant_id,
                project_id=project_id,
                resource_id=resource_id,
                resource_ref=event.resource_ref.model_dump(mode="json"),
                type=event.event_type,
                occurred_at=event.occurred_at,
                observed_at=event.observed_at,
                severity=event.severity,
                payload=payload,
                correlation=(
                    {k: v for k, v in event.correlation.model_dump().items() if v is not None}
                    if event.correlation
                    else None
                ),
                source=event.source,
                provenance=event.provenance,
                raw_ref=event.raw_ref,
                resolution_state="unresolved" if resource_id is None else "resolved",
                scenario_name=event.scenario.name if event.scenario else None,
                scenario_offset=event.scenario.offset if event.scenario else None,
            )
        )

    if to_insert:
        session.add_all(to_insert)
        await session.flush()

    return IngestionResult(
        accepted=len(to_insert),
        duplicate=duplicate,
        unresolved=unresolved,
        rejected=0,
    )


async def resolve_pending_events(session: AsyncSession, project_id: uuid.UUID) -> int:
    events = (
        (
            await session.execute(
                select(Event)
                .where(Event.project_id == project_id, Event.resolution_state == "unresolved")
                .limit(1000)
            )
        )
        .scalars()
        .all()
    )
    resolved = 0
    for event in events:
        ref = event.resource_ref
        key = (ref["namespace"], ref["external_id"])
        aliases = await _resolve_aliases(
            session, {key}, tenant_id=event.tenant_id, project_id=project_id, at=event.occurred_at
        )
        if key in aliases:
            event.resource_id = aliases[key]
            event.resolution_state = "resolved"
            resolved += 1
    if resolved:
        await session.flush()
    return resolved
