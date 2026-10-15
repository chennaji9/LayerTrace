"""工作负载 API（T087）。

容器状态、限制、实时指标、共享延迟基线与 Agent 超时、来源、stale/gap、
Grafana 深链。数据陈旧或缺失由 state/data_state 明确表达，禁止伪装健康。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.pagination import apply_descending_cursor, encode_cursor
from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.db import get_session
from apps.control_plane.diagnosis.association import (
    affected_instances,
    baseline_instance_starts,
    owning_containers,
)
from apps.control_plane.diagnosis.baseline import alert_exclusion_windows, compute_baseline
from apps.control_plane.inventory.temporal_graph import effective_topology
from apps.control_plane.models import Event, Resource
from packages.contracts.schemas import LatencyBaseline, WorkloadOut, WorkloadPage

router = APIRouter(prefix="/api/v1", tags=["Workloads"])


def _data_state(resource_last_seen: datetime, now: datetime) -> str:
    from apps.control_plane.config import get_settings

    settings = get_settings()
    stale_after = settings.heartbeat_interval_seconds * settings.stale_after_missed_heartbeats
    age = (now - resource_last_seen).total_seconds()
    if age > stale_after * 2:
        return "gap"
    if age > stale_after:
        return "stale"
    return "current"


@router.get("/workloads", response_model=WorkloadPage)
async def list_workloads(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    state: str | None = Query(default=None),
    provenance: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> WorkloadPage:
    now = datetime.now(UTC)
    query = apply_descending_cursor(
        select(Resource).where(
            Resource.project_id == principal.project_id, Resource.kind == "container"
        ),
        Resource.last_seen,
        Resource.id,
        cursor,
    )
    if provenance:
        query = query.where(Resource.provenance == provenance)
    containers = (
        (await session.execute(query.order_by(Resource.last_seen.desc(), Resource.id.desc())))
        .scalars()
        .all()
    )
    settings_grafana = None
    from apps.control_plane.config import get_settings

    settings = get_settings()
    settings_grafana = settings.grafana_url

    items: list[WorkloadOut] = []
    returned_containers = []
    for container in containers:
        container_state = str(container.attributes.get("state") or "unknown")
        if container.deleted_at is not None:
            container_state = "stopped"
        elif container_state not in ("running", "stopped"):
            container_state = "unknown"
        # 最近一次容器指标事件（实时指标摘要）
        metric_event = (
            await session.execute(
                select(Event)
                .where(
                    Event.resource_id == container.id,
                    Event.type.in_(["container.stats.sample"]),
                )
                .order_by(Event.occurred_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        metrics = (
            dict(metric_event.payload)
            if metric_event
            else dict(container.attributes.get("metrics") or {})
        )
        observed_at = metric_event.occurred_at if metric_event else container.last_seen
        data_state = _data_state(observed_at, now)
        if data_state != "current":
            container_state = "unknown"
        if state and container_state != state:
            continue

        nodes, edges, _ = await effective_topology(
            session, principal.project_id, now, root_id=container.id
        )
        causal_context = {
            "symptom_resource_id": str(container.id),
            "topology_nodes": [
                {"id": str(node.id), "kind": node.kind, "first_seen": node.first_seen.isoformat()}
                for node in nodes
            ],
            "topology_edges": [
                {
                    "from_id": str(edge.from_id),
                    "type": edge.type,
                    "to_id": str(edge.to_id),
                    "valid_from": edge.valid_from.isoformat(),
                }
                for edge in edges
            ],
        }
        import uuid

        service_ids = affected_instances(causal_context)
        exclusion_windows = await alert_exclusion_windows(
            session,
            project_id=principal.project_id,
            now=now,
            r2_resource_ids=[container.id],
            resource_ids=[
                uuid.UUID(key)
                for key in (service_ids | owning_containers(causal_context, service_ids))
            ],
        )
        baseline_raw = await compute_baseline(
            session,
            project_id=principal.project_id,
            now=now,
            service_instance_ids=[uuid.UUID(key) for key in service_ids],
            exclude_incident_windows=exclusion_windows,
            service_instance_not_before={
                uuid.UUID(key): value
                for key, value in baseline_instance_starts(causal_context).items()
            },
        )
        baseline = LatencyBaseline(
            state=baseline_raw["state"],  # type: ignore[arg-type]
            window_seconds=baseline_raw["window_seconds"],
            sample_count=baseline_raw["sample_count"],
            p95_ms=baseline_raw["p95_ms"],
            multiplier=baseline_raw["multiplier"],
            agent_timeout_ms=baseline_raw["agent_timeout_ms"],
        )

        limits = dict(container.attributes.get("limits") or {})
        if not limits:
            limits = {k: v for k, v in container.attributes.items() if "limit" in k or "quota" in k}

        # Grafana 深链（配置了才提供）
        dashboard_url = None
        if settings_grafana:
            dashboard_url = f"{settings_grafana}/d/layertrace-containers?var-container={container.source_id[:12]}"

        items.append(
            WorkloadOut(
                id=str(container.id),
                container_id=container.source_id,
                display_name=container.display_name or container.source_id[:12],
                state=container_state,  # type: ignore[arg-type]
                data_state=data_state,  # type: ignore[arg-type]
                limits=limits,
                metrics=metrics,
                latency_baseline=baseline,
                dashboard_url=dashboard_url,
                provenance=container.provenance,  # type: ignore[arg-type]
                observed_at=observed_at,
            )
        )
        returned_containers.append(container)
        if len(items) > limit:
            break

    next_cursor = (
        encode_cursor(
            returned_containers[limit - 1].last_seen.isoformat(),
            str(returned_containers[limit - 1].id),
        )
        if len(items) > limit
        else None
    )
    return WorkloadPage(items=items[:limit], next_cursor=next_cursor)
