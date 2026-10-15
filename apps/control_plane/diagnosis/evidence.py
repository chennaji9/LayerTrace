"""证据持久化（T041）。

- 证据保存摘要与查询参数，不复制全部原始数据；
- source_ref 稳定（event:<uuid> / snapshot:<uuid> / metric:<指纹>）；
- 记录过期时间；原始数据过期后记录保留并标记 source_expired=true。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.models import Evidence

# 事件证据允许复制的属性键（摘要）
_EVENT_SUMMARY_KEYS = (
    "oom_killed",
    "exit_code",
    "oom_kill_delta",
    "available_ratio",
    "used_bytes",
    "memory_limit_bytes",
    "error_class",
    "phase",
    "signal",
    "reason",
    "state",
    "cpu_percent",
    "quota",
    "period_us",
    "throttled_periods",
    "http_status",
    "tool_name",
    "model_name",
    "duration_ms",
    "model_duration_ms",
    "tool_duration_ms",
    "cpu_quota",
    "cpu_period_us",
    "throttled_periods_delta",
    "throttled_seconds_delta",
    "memory_working_set_bytes",
    "deadline_exceeded",
    "observer",
    "cpu_limit_cores",
    "throttled_ratio",
    "cpu_periods_delta",
    "throttled_time_ns_delta",
)


def _summary_from_payload(payload: dict) -> dict:
    return {k: payload[k] for k in _EVENT_SUMMARY_KEYS if k in payload}


async def persist_event_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    event: dict,
    resource_id: str | None,
) -> Evidence:
    """为诊断引用的事件建立证据记录。幂等（source_ref 唯一）。"""
    settings = get_settings()
    source_ref = f"event:{event['id']}"
    existing = (
        await session.execute(select(Evidence).where(Evidence.source_ref == source_ref))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    occurred = datetime.fromisoformat(event["occurred_at"])
    observed = datetime.fromisoformat(event["observed_at"])
    evidence = Evidence(
        # 事件证据复用事件 UUID 作为主键，诊断引用与事实一一对应
        id=uuid.UUID(event["id"]),
        tenant_id=tenant_id,
        project_id=project_id,
        kind="event",
        resource_id=uuid.UUID(resource_id) if resource_id else None,
        range_start=occurred,
        range_end=max(occurred, observed),
        source_ref=source_ref,
        summary=_summary_from_payload(event.get("payload", {})),
        query_parameters={
            "event_id": event["id"],
            "event_type": event.get("type"),
            "source": event.get("source"),
            "task_id": (event.get("correlation") or {}).get("task_id"),
        },
        provenance=event.get("provenance", "observed"),
        expires_at=datetime.now(UTC) + timedelta(days=settings.retention_event_days),
        retention_days=settings.retention_event_days,
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def persist_snapshot_evidence(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    project_id: uuid.UUID,
    snapshot: dict,
) -> Evidence | None:
    settings = get_settings()
    source_ref = f"snapshot:{snapshot['id']}"
    existing = (
        await session.execute(select(Evidence).where(Evidence.source_ref == source_ref))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    captured = datetime.fromisoformat(snapshot["captured_at"])
    evidence = Evidence(
        id=uuid.UUID(snapshot["id"]),
        tenant_id=tenant_id,
        project_id=project_id,
        kind="snapshot",
        resource_id=uuid.UUID(snapshot["resource_id"]),
        range_start=captured,
        range_end=captured,
        source_ref=source_ref,
        summary={
            "limits": snapshot.get("limits", {}),
            "image_digest": snapshot.get("image_digest"),
        },
        query_parameters={"config_hash": snapshot.get("config_hash")},
        provenance=snapshot.get("provenance", "observed"),
        expires_at=datetime.now(UTC) + timedelta(days=settings.retention_event_days),
        retention_days=settings.retention_event_days,
    )
    session.add(evidence)
    await session.flush()
    return evidence


async def mark_expired_evidence(session: AsyncSession, *, now: datetime | None = None) -> int:
    """原始数据过期后保留摘要并标记 source_expired。"""
    now = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(Evidence).where(
                    Evidence.source_expired.is_(False),
                    Evidence.expires_at.isnot(None),
                    Evidence.expires_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.source_expired = True
        row.summary = {
            **row.summary,
            "source_expired_notice": "原始证据已超过保留期；当前仅保留采集时生成的摘要。",
        }
    if rows:
        await session.flush()
    return len(rows)
