"""延迟基线服务（T083）。

R2/R4/Agent 共用：最近 15 分钟排除已有事件单时段后的成功任务延迟 P95；
样本少于 30 输出 insufficient；Agent 超时 = 基线 P95 三倍。
禁止各 evaluator 或 Agent 复制这套计算。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.models import Alert, Diagnosis, Incident, TaskRun


async def alert_exclusion_windows(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    now: datetime,
    resource_ids: list[uuid.UUID],
    r2_resource_ids: list[uuid.UUID] | None = None,
) -> list[tuple[datetime, datetime]]:
    """Exclude strong faults; bare throttling cannot prevent baseline bootstrap."""
    if not resource_ids:
        return []
    settings = get_settings()
    stable = timedelta(seconds=settings.incident_recovery_stable_seconds)
    window_start = now - timedelta(seconds=settings.baseline_window_seconds)
    rows = (
        await session.execute(
            select(Alert, Incident.id)
            .join(
                Incident,
                (Incident.project_id == Alert.project_id)
                & (Incident.fingerprint == Alert.fingerprint),
            )
            .where(
                Alert.project_id == project_id,
                Alert.resource_id.in_(resource_ids),
                Alert.starts_at <= now,
                (Alert.ends_at.is_(None) | (Alert.ends_at >= window_start - stable)),
                Alert.state.in_(("firing", "resolved")),
            )
        )
    ).all()
    r2_resources = set(resource_ids if r2_resource_ids is None else r2_resource_ids)
    r2_incidents = {
        incident_id
        for alert, incident_id in rows
        if alert.rule_id == "R2" and alert.resource_id in r2_resources
    }
    confirmed = {}
    if r2_incidents:
        revisions = (
            (
                await session.execute(
                    select(Diagnosis).where(
                        Diagnosis.incident_id.in_(r2_incidents),
                        Diagnosis.created_at <= now,
                        Diagnosis.candidates.contains([{"rule_id": "R2", "support_level": "high"}]),
                    )
                )
            )
            .scalars()
            .all()
        )
        for revision in revisions:
            confirmed.setdefault(revision.incident_id, []).append(revision)
    windows = []
    for alert, incident_id in rows:
        if alert.rule_id == "R2":
            if alert.resource_id not in r2_resources:
                continue
            matching = False
            for revision in confirmed.get(incident_id, []):
                if revision.symptom_resource_id != alert.resource_id:
                    continue
                citations = {
                    str(key)
                    for candidate in revision.candidates
                    if candidate.get("rule_id") == "R2" and candidate.get("support_level") == "high"
                    for key in candidate.get("supporting", [])
                }
                if alert.source == "event":
                    matching = (alert.source_alert_id or "").removeprefix("event:") in citations
                else:
                    matching = revision.window_start <= alert.starts_at <= revision.window_end
                if matching:
                    break
            if not matching:
                continue
        windows.append(
            (alert.starts_at, (alert.ends_at + stable) if alert.ends_at is not None else now)
        )
    return windows


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


async def compute_baseline(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    now: datetime,
    exclude_incident_windows: list[tuple[datetime, datetime]] | None = None,
    service_instance_ids: list[uuid.UUID] | None = None,
    service_instance_not_before: dict[uuid.UUID, datetime] | None = None,
    duration_field: str = "duration_ms",
) -> dict:
    """最近 15 分钟成功任务的延迟基线（排除事件单时段）。"""
    settings = get_settings()
    window_seconds = settings.baseline_window_seconds
    min_samples = settings.baseline_min_samples
    window_start = now - timedelta(seconds=window_seconds)

    duration_column = (
        TaskRun.model_duration_ms if duration_field == "model_duration_ms" else TaskRun.duration_ms
    )
    query = select(
        TaskRun.service_instance_id, TaskRun.started_at, TaskRun.ended_at, duration_column
    ).where(
        TaskRun.project_id == project_id,
        TaskRun.status == "succeeded",
        duration_column.isnot(None),
        TaskRun.started_at >= window_start,
        TaskRun.started_at <= now,
        TaskRun.ended_at <= now,
    )
    if service_instance_ids is not None:
        query = query.where(TaskRun.service_instance_id.in_(service_instance_ids))
    rows = (await session.execute(query)).all()

    durations: list[float] = []
    for instance_id, started_at, ended_at, duration_ms in rows:
        eligible_start = (service_instance_not_before or {}).get(instance_id)
        if eligible_start is not None and started_at < eligible_start:
            continue
        if exclude_incident_windows and any(
            started_at <= end and ended_at >= start for start, end in exclude_incident_windows
        ):
            continue
        durations.append(float(duration_ms))

    sample_count = len(durations)
    if sample_count < min_samples:
        return {
            "state": "insufficient",
            "window_seconds": window_seconds,
            "sample_count": sample_count,
            "p95_ms": None,
            "multiplier": settings.agent_timeout_multiplier,
            "agent_timeout_ms": None,
            "duration_field": duration_field,
            "window_end": now.isoformat(),
        }

    p95 = _percentile(durations, 0.95)
    return {
        "state": "ready",
        "window_seconds": window_seconds,
        "sample_count": sample_count,
        "p95_ms": round(p95, 1),
        "multiplier": settings.agent_timeout_multiplier,
        "agent_timeout_ms": int(p95 * settings.agent_timeout_multiplier),
        "duration_field": duration_field,
        "window_end": now.isoformat(),
    }
