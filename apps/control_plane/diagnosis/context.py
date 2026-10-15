"""诊断上下文构建（T039）。

症状前 120 秒至后 30 秒窗口内，汇聚拓扑节点上的事件、任务、快照、告警与证据；
时钟偏差（观测时间与发生时间之差）超过 5 秒时，时间可信度降级为 degraded。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.inventory.temporal_graph import effective_topology
from apps.control_plane.models import (
    Alert,
    Event,
    Incident,
    ResourceSnapshot,
    TaskRun,
)


def _serialize_event(event: Event) -> dict:
    return {
        "id": str(event.id),
        "type": event.type,
        "resource_id": str(event.resource_id) if event.resource_id else None,
        "resource_ref": event.resource_ref,
        "occurred_at": event.occurred_at.isoformat(),
        "observed_at": event.observed_at.isoformat(),
        "severity": event.severity,
        "payload": dict(event.payload or {}),
        "correlation": dict(event.correlation or {}),
        "source": event.source,
        "provenance": event.provenance,
    }


def _serialize_task(task: TaskRun) -> dict:
    return {
        "id": str(task.id),
        "task_id": task.task_id,
        "service_instance_id": str(task.service_instance_id),
        "status": task.status,
        "status_provenance": task.status_provenance,
        "error_class": task.error_class,
        "started_at": task.started_at.isoformat(),
        "ended_at": task.ended_at.isoformat() if task.ended_at else None,
        "duration_ms": task.duration_ms,
        "model_duration_ms": task.model_duration_ms,
        "tool_duration_ms": task.tool_duration_ms,
        "correlation": {"task_id": task.task_id},
    }


def _baseline_episode_start(
    incident: Incident, alerts: list[Alert], stable_seconds: int
) -> datetime:
    """Keep the pre-fault anchor fixed within each reopened fault episode.

    Alert intervals separated by the recovery stability gate identify a new
    episode without rewriting the incident's historical first_seen fact.
    """
    if not incident.reopen_count:
        return incident.first_seen
    marked = [
        datetime.fromisoformat(row.annotations["incident_episode_started_at"])
        for row in alerts
        if (getattr(row, "annotations", None) or {}).get("incident_reopen_count")
        == incident.reopen_count
        and (row.annotations or {}).get("incident_episode_started_at")
        and row.starts_at <= incident.last_seen
    ]
    if marked:
        return min(marked)
    episode_start = incident.first_seen
    previous_end = None
    for alert in sorted(alerts, key=lambda row: row.starts_at):
        if alert.starts_at > incident.last_seen:
            continue
        if previous_end is None or (
            alert.starts_at - previous_end >= timedelta(seconds=stable_seconds)
        ):
            episode_start = alert.starts_at
        end = min(alert.ends_at or incident.last_seen, incident.last_seen)
        previous_end = max(previous_end or end, end)
    return episode_start


async def build_context(session: AsyncSession, incident: Incident) -> DiagnosticContextT:
    from apps.control_plane.integrations.protocols import DiagnosticContext

    settings = get_settings()
    symptom_time = incident.last_seen
    window_start = symptom_time - timedelta(seconds=settings.diagnosis_window_before_seconds)
    window_end = symptom_time + timedelta(seconds=settings.diagnosis_window_after_seconds)

    # 症状资源：事件单指纹对应的稳定故障对象（事件告警创建时使用告警的 resource_id）
    alert_rows = (
        (
            await session.execute(
                select(Alert)
                .where(
                    Alert.project_id == incident.project_id,
                    Alert.fingerprint == incident.fingerprint,
                )
                .order_by(Alert.starts_at)
            )
        )
        .scalars()
        .all()
    )
    symptom_resource_id = next(
        (str(a.resource_id) for a in alert_rows if a.resource_id is not None), None
    )

    nodes, edges, truncated = await effective_topology(
        session,
        incident.project_id,
        symptom_time,
        root_id=uuid.UUID(symptom_resource_id) if symptom_resource_id else None,
    )
    node_ids = [n.id for n in nodes]

    events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.project_id == incident.project_id,
                    Event.resource_id.in_(node_ids),
                    Event.occurred_at >= window_start,
                    Event.occurred_at <= window_end,
                )
                .order_by(Event.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )
    event_dicts = [_serialize_event(e) for e in events]

    tasks = (
        (
            await session.execute(
                select(TaskRun)
                .where(
                    TaskRun.project_id == incident.project_id,
                    TaskRun.service_instance_id.in_(node_ids),
                )
                .order_by(TaskRun.started_at.asc())
            )
        )
        .scalars()
        .all()
    )
    task_dicts = [
        _serialize_task(t)
        for t in tasks
        if t.started_at <= window_end and (t.ended_at is None or t.ended_at >= window_start)
    ]

    snapshots = (
        (
            await session.execute(
                select(ResourceSnapshot)
                .where(ResourceSnapshot.resource_id.in_(node_ids))
                .order_by(ResourceSnapshot.captured_at.desc())
            )
        )
        .scalars()
        .all()
    )
    seen_resources: set = set()
    snapshot_dicts = []
    for snap in snapshots:
        if snap.resource_id in seen_resources:
            continue  # 每资源取窗口内最近一份
        if snap.captured_at <= window_end:
            seen_resources.add(snap.resource_id)
            snapshot_dicts.append(
                {
                    "id": str(snap.id),
                    "resource_id": str(snap.resource_id),
                    "captured_at": snap.captured_at.isoformat(),
                    "limits": dict(snap.limits or {}),
                    "image_digest": snap.image_digest,
                    "config_hash": snap.config_hash,
                    "provenance": snap.provenance,
                }
            )

    # 时间可信度：观测时间与发生时间最大偏差超过阈值即降级
    max_skew = max(
        (abs((e.observed_at - e.occurred_at).total_seconds()) for e in events),
        default=0.0,
    )
    time_confidence = "trusted" if max_skew <= settings.clock_skew_degrade_seconds else "degraded"

    # 共享延迟基线（R2/R4/Agent 使用同一服务，禁止复制计算）
    from apps.control_plane.diagnosis.association import (
        affected_instances,
        baseline_instance_starts,
        owning_containers,
    )
    from apps.control_plane.diagnosis.baseline import alert_exclusion_windows, compute_baseline
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import model_phase_matches_symptom

    causal_context = {
        "symptom_resource_id": symptom_resource_id,
        "topology_nodes": [
            {
                "id": str(n.id),
                "kind": n.kind,
                "source_id": n.source_id,
                "first_seen": n.first_seen.isoformat(),
            }
            for n in nodes
        ],
        "topology_edges": [
            {
                "from_id": str(e.from_id),
                "type": e.type,
                "to_id": str(e.to_id),
                "valid_from": e.valid_from.isoformat(),
            }
            for e in edges
        ],
    }
    service_ids = affected_instances(causal_context)
    not_before = {
        uuid.UUID(key): value for key, value in baseline_instance_starts(causal_context).items()
    }
    baseline_time = _baseline_episode_start(
        incident, alert_rows, settings.incident_recovery_stable_seconds
    ) - timedelta(microseconds=1)
    exclusion_windows = await alert_exclusion_windows(
        session,
        project_id=incident.project_id,
        now=baseline_time,
        r2_resource_ids=[uuid.UUID(symptom_resource_id)] if symptom_resource_id else [],
        resource_ids=[
            uuid.UUID(key) for key in (service_ids | owning_containers(causal_context, service_ids))
        ],
    )
    baseline = await compute_baseline(
        session,
        project_id=incident.project_id,
        now=baseline_time,
        service_instance_ids=[uuid.UUID(key) for key in service_ids],
        service_instance_not_before=not_before,
        exclude_incident_windows=exclusion_windows,
        duration_field="model_duration_ms"
        if any(model_phase_matches_symptom(event, causal_context) for event in event_dicts)
        else "duration_ms",
    )
    if baseline["state"] != "ready" and baseline.get("duration_field") == "model_duration_ms":
        baseline = await compute_baseline(
            session,
            project_id=incident.project_id,
            now=baseline_time,
            service_instance_ids=[uuid.UUID(key) for key in service_ids],
            service_instance_not_before=not_before,
            exclude_incident_windows=exclusion_windows,
        )

    return DiagnosticContext(
        symptom_resource_id=symptom_resource_id,
        incident_id=str(incident.id),
        symptom_time=symptom_time,
        window=(window_start, window_end),
        events=event_dicts,
        tasks=task_dicts,
        snapshots=snapshot_dicts,
        alerts=[
            {
                "id": str(a.id),
                "rule_id": a.rule_id,
                "state": a.state,
                "severity": a.severity,
                "starts_at": a.starts_at.isoformat(),
                "ends_at": a.ends_at.isoformat() if a.ends_at else None,
            }
            for a in alert_rows
        ],
        evidence=[],
        topology_nodes=[
            {
                "id": str(n.id),
                "kind": n.kind,
                "source_id": n.source_id,
                "display_name": n.display_name,
                "provenance": n.provenance,
                "first_seen": n.first_seen.isoformat(),
            }
            for n in nodes
        ],
        topology_edges=[
            {
                "id": str(r.id),
                "from_id": str(r.from_id),
                "type": r.type,
                "to_id": str(r.to_id),
                "source": r.source,
                "provenance": r.provenance,
                "valid_from": r.valid_from.isoformat(),
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
            }
            for r in edges
        ],
        time_confidence=time_confidence,
        context_truncated=truncated,
        baseline=baseline,
    )


DiagnosticContextT = object  # 类型别名占位（真实类型见 integrations.protocols）
