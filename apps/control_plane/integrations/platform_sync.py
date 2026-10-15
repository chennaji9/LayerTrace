"""Persist inventory and native event cursor atomically; one worker is the owner."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.ingestion.repository import ingest_batch, resolve_pending_events
from apps.control_plane.integrations.status_service import upsert_status
from apps.control_plane.inventory.service import bind_alias, upsert_relationship, upsert_resource
from apps.control_plane.models import IngestionCursor, Relationship, Resource
from integrations.zsvirt.provider import ZSvirtProvider
from packages.contracts.schemas import EventBatch, EventInput


async def _sync_facts(session: AsyncSession, provider: ZSvirtProvider, tenant, project) -> dict:
    now = datetime.now(UTC)
    batch = await provider.discover()
    by_source: dict[str, Resource] = {}
    for item in batch.resources:
        res = await upsert_resource(
            session,
            tenant_id=tenant,
            project_id=project,
            kind=item.kind,
            provider=item.provider,
            provider_instance=item.provider_instance,
            source_id=item.source_id,
            display_name=item.display_name,
            attributes=item.attributes,
            provenance=item.provenance,
            now=now,
        )
        # UUID lifecycle is authoritative; current host placement is only observed now.
        created = item.attributes.get("platform_created_at")
        alias_start = min(datetime.fromisoformat(created), now) if created else now
        for namespace, external_id in [
            (item.namespace, item.external_id),
            ("zsvirt.resource", item.source_id),
        ]:
            alias = await bind_alias(
                session,
                resource_id=res.id,
                namespace=namespace,
                external_id=external_id,
                valid_from=alias_start,
                provenance="observed",
            )
            if created and alias is not None and alias.valid_from > alias_start:
                alias.valid_from = alias_start
        by_source[item.source_id] = res
    for rel in batch.relationships:
        parent, child = by_source.get(rel.from_source_id), by_source.get(rel.to_source_id)
        if parent is None or child is None:
            continue
        old_edges = (
            (
                await session.execute(
                    select(Relationship).where(
                        Relationship.to_id == child.id,
                        Relationship.type == "hosts",
                        Relationship.from_id != parent.id,
                        Relationship.valid_to.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for edge in old_edges:
            edge.valid_to = now
        await upsert_relationship(
            session,
            tenant_id=tenant,
            project_id=project,
            from_id=parent.id,
            type_="hosts",
            to_id=child.id,
            valid_from=now,
            valid_to=None,
            source="zsvirt",
            provenance="observed",
            from_kind="host",
            to_kind="vm",
        )
    cursor = (
        await session.execute(
            select(IngestionCursor).where(
                IngestionCursor.provider_instance == provider.instance,
                IngestionCursor.stream == "platform-events",
            )
        )
    ).scalar_one_or_none()
    event_batch = await provider.read_events(cursor.cursor if cursor else None)
    accepted = 0
    if event_batch.events:
        events = [
            EventInput(
                schema_version="1.0",
                event_id=e.event_id,
                event_type=e.event_type,
                resource_ref={"namespace": e.namespace, "external_id": e.external_id},
                occurred_at=e.occurred_at,
                observed_at=e.observed_at,
                severity=e.severity,
                source=e.source,
                provenance=e.provenance,
                attributes=e.attributes,
                raw_ref=e.raw_ref,
            )
            for e in event_batch.events
        ]
        result = await ingest_batch(
            session,
            tenant_id=tenant,
            project_id=project,
            batch=EventBatch(schema_version="1.0", events=events),
        )
        accepted = result.accepted
    if event_batch.next_cursor:
        if cursor is None:
            session.add(
                IngestionCursor(
                    provider_instance=provider.instance,
                    stream="platform-events",
                    cursor=event_batch.next_cursor,
                    committed_at=now,
                )
            )
        else:
            cursor.cursor, cursor.committed_at = event_batch.next_cursor, now
    await resolve_pending_events(session, project)
    return {"resources": len(by_source), "events": accepted}


async def sync_platform(session: AsyncSession) -> dict:
    settings = get_settings()
    provider = ZSvirtProvider(
        base_url=settings.zsvirt_base_url,
        token=settings.zsvirt_token,
        account_name=settings.zsvirt_account_name,
        password_hash=settings.zsvirt_password_hash,
        ca_path=settings.zsvirt_ca_path,
        provider_instance=settings.zsvirt_provider_instance,
    )
    try:
        async with session.begin_nested():
            counts = await _sync_facts(
                session, provider, uuid.UUID(settings.tenant_id), uuid.UUID(settings.project_id)
            )
    except Exception:
        health = provider.health()
        await upsert_status(
            session,
            source_type="zsvirt",
            source_instance="zsvirt",
            state=health.state if health.state != "healthy" else "degraded",
            error_summary=health.error_summary
            or "ZSvirt synchronization failed; previous facts retained",
            error_code=health.error_code,
            missing_permissions=health.missing_permissions,
        )
        await session.commit()
        raise
    health, caps = provider.health(), provider.capabilities()
    await upsert_status(
        session,
        source_type="zsvirt",
        source_instance="zsvirt",
        state=health.state,
        last_success_at=health.last_success_at,
        latency_ms=health.latency_ms,
        error_summary=health.error_summary or "",
        missing_permissions=health.missing_permissions,
        error_code=health.error_code,
        capabilities=asdict(caps),
    )
    await session.commit()
    return counts
