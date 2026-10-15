"""数据源状态汇总服务（T084）。

- 连接状态、最后成功、延迟、错误、权限、能力、未解析数、积压、丢弃、后端可用性；
- 超过三个心跳周期未更新至少进入 stale（worker 调用 mark_stale_sources）；
- 后端失败按源隔离，不拖垮其余诊断接口。
"""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.models import Resource, SourceStatus


async def mark_stale_sources(session: AsyncSession) -> int:
    settings = get_settings()
    threshold = settings.heartbeat_interval_seconds * settings.stale_after_missed_heartbeats
    cutoff = datetime.now(UTC) - timedelta(seconds=threshold)
    result = await session.execute(
        update(SourceStatus)
        .where(
            SourceStatus.state.in_(["healthy", "degraded"]),
            SourceStatus.updated_at < cutoff,
        )
        .values(state="stale")
    )
    await session.flush()
    return result.rowcount or 0


async def upsert_status(
    session: AsyncSession,
    *,
    source_type: str,
    source_instance: str,
    state: str,
    last_success_at: datetime | None = None,
    latency_ms: int | None = None,
    error_code: str | None = None,
    error_summary: str | None = None,
    missing_permissions: list | None = None,
    capabilities: dict | None = None,
    unresolved_count: int | None = None,
    queue_lag_seconds: float | None = None,
    dropped_count: int | None = None,
    data_gaps: list | None = None,
) -> SourceStatus:
    row = (
        await session.execute(
            select(SourceStatus).where(
                SourceStatus.source_type == source_type,
                SourceStatus.source_instance == source_instance,
            )
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if row is None:
        row = SourceStatus(
            source_type=source_type,
            source_instance=source_instance,
            state=state,
            updated_at=now,
        )
        session.add(row)
    else:
        row.state = state
        row.updated_at = now
    if last_success_at is not None:
        row.last_success_at = last_success_at
    if latency_ms is not None:
        row.latency_ms = latency_ms
    if error_code is not None:
        row.error_code = error_code
    if error_summary is not None:
        row.error_summary = error_summary
    if missing_permissions is not None:
        row.missing_permissions = missing_permissions
    if capabilities is not None:
        row.capabilities = capabilities
    if unresolved_count is not None:
        row.unresolved_count = unresolved_count
    if queue_lag_seconds is not None:
        row.queue_lag_seconds = queue_lag_seconds
    if dropped_count is not None:
        row.dropped_count = dropped_count
    if data_gaps is not None:
        row.data_gaps = data_gaps
    await session.flush()
    return row


async def check_postgres(session: AsyncSession) -> dict:
    started = time.monotonic()
    try:
        await session.execute(select(1))
        latency = int((time.monotonic() - started) * 1000)
        return {"state": "healthy", "latency_ms": latency, "last_success_at": datetime.now(UTC)}
    except Exception as exc:  # noqa: BLE001 - 状态隔离
        return {"state": "unavailable", "error_summary": str(exc)[:200]}


async def check_prometheus(session: AsyncSession) -> dict:
    import httpx

    settings = get_settings()
    if not settings.prometheus_url:
        return {"state": "not_configured"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(f"{settings.prometheus_url}/-/ready")
        if res.status_code == 200:
            return {
                "state": "healthy",
                "latency_ms": int((time.monotonic() - started) * 1000),
                "last_success_at": datetime.now(UTC),
            }
        return {"state": "degraded", "error_summary": f"HTTP {res.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"state": "unavailable", "error_summary": str(exc)[:200]}


async def summarize(session: AsyncSession, *, project_id: uuid.UUID) -> dict:
    """汇总全部数据源状态（未配置组件明确 not_configured）。"""
    settings = get_settings()

    pg = await check_postgres(session)
    await upsert_status(
        session, source_type="postgres", source_instance=settings.instance_name, **pg
    )

    prom = await check_prometheus(session)
    await upsert_status(session, source_type="prometheus", source_instance="prometheus", **prom)

    if not settings.zsvirt_enabled:
        await upsert_status(
            session,
            source_type="zsvirt",
            source_instance="zsvirt",
            state="not_configured",
        )
    else:
        # The worker owns collection timestamps; page reads must not refresh stale data.
        platform_status = (await session.execute(select(SourceStatus).where(
            SourceStatus.source_type == "zsvirt", SourceStatus.source_instance == "zsvirt",
        ))).scalar_one_or_none()
        if platform_status is None:
            await upsert_status(session, source_type="zsvirt", source_instance="zsvirt",
                                state="degraded", error_summary="Waiting for first platform synchronization")

    # 陈旧标记 + 未解析计数
    await mark_stale_sources(session)
    from apps.control_plane.inventory.unresolved import count_unresolved

    unresolved_total = await count_unresolved(session, project_id)

    rows = (
        await session.execute(select(SourceStatus).order_by(SourceStatus.source_type))
    ).scalars().all()
    allowed_vms = set(str(value) for value in (await session.execute(
        select(Resource.id).where(Resource.project_id == project_id, Resource.kind == "vm")
    )).scalars())
    sources = []
    for row in rows:
        if row.source_type == "probe" and str((row.capabilities or {}).get("vm_id", "")) not in allowed_vms:
            continue
        sources.append(
            {
                "type": row.source_type,
                "instance": row.source_instance,
                "state": row.state,
                "last_success_at": row.last_success_at,
                "latency_ms": row.latency_ms,
                "error_summary": row.error_summary,
                "missing_permissions": list(row.missing_permissions or []),
                "capabilities": {key: value for key, value in (row.capabilities or {}).items()
                                 if not any(fragment in key.lower() for fragment in ("credential", "secret", "token", "password"))},
                "unresolved_count": row.unresolved_count,
                "queue_lag_seconds": row.queue_lag_seconds,
                "dropped_count": row.dropped_count,
                "data_gaps": [
                    {"start": g.get("start"), "end": g.get("end")}
                    for g in (row.data_gaps or [])
                ],
            }
        )
    return {"sources": sources, "unresolved_count": unresolved_total}
