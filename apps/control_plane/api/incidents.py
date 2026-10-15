"""事件单 API（T047 + T103 列表基础）。

- GET /api/v1/incidents：分页 + state/severity 过滤 + 降噪计数；
- GET /api/v1/incidents/{id}：详情（告警、时间线、最新诊断）；
- GET /api/v1/incidents/{id}/diagnoses：全部修订按 revision 升序。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.api.evidence import evidence_out
from apps.control_plane.api.pagination import apply_descending_cursor, encode_cursor
from apps.control_plane.auth import Principal, get_principal, require_scope
from apps.control_plane.db import get_session
from apps.control_plane.models import Alert, Diagnosis, Event, Evidence, Incident
from packages.contracts.schemas import (
    DiagnosisCandidate,
    DiagnosisOut,
    IncidentDetail,
    IncidentPage,
    IncidentSummary,
    SilenceOut,
    SilenceRequest,
)

router = APIRouter(prefix="/api/v1", tags=["Incidents"])


class AcknowledgeRequest(BaseModel):
    actor: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


def _summary(incident: Incident, silenced_until: datetime | None = None) -> IncidentSummary:
    return IncidentSummary(
        id=str(incident.id),
        fingerprint=incident.fingerprint,
        state=incident.state,  # type: ignore[arg-type]
        severity=incident.severity,  # type: ignore[arg-type]
        first_seen=incident.first_seen,
        last_seen=incident.last_seen,
        alert_count=incident.alert_count,
        silenced_until=silenced_until,
    )


def _diagnosis_out(diag: Diagnosis) -> DiagnosisOut:
    return DiagnosisOut(
        id=str(diag.id),
        incident_id=str(diag.incident_id),
        revision=diag.revision,
        candidates=[
            DiagnosisCandidate(**{**c, "support_level": c["support_level"]})  # type: ignore[arg-type]
            for c in diag.candidates
        ],
        supporting_evidence_ids=[str(e) for e in diag.supporting_evidence_ids],
        contradicting_evidence_ids=[str(e) for e in diag.contradicting_evidence_ids],
        missing_evidence=list(diag.missing_evidence),
        observed_impact=dict(diag.observed_impact),
        potential_impact=dict(diag.potential_impact),
        rule_versions={str(k): str(v) for k, v in diag.rule_versions.items()},
        time_confidence=diag.time_confidence,  # type: ignore[arg-type]
        context_truncated=diag.context_truncated,
        created_at=diag.created_at,
    )


async def _get_incident(
    session: AsyncSession, incident_id: uuid.UUID, principal: Principal
) -> Incident:
    incident = (
        await session.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None or str(incident.project_id) != str(principal.project_id):
        raise ApiError(404, "not_found", "对象不存在或不在认证范围内")
    return incident


@router.get("/incidents", response_model=IncidentPage)
async def list_incidents(
    state: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> IncidentPage:
    query = select(Incident).where(Incident.project_id == principal.project_id)
    if state:
        query = query.where(Incident.state == state)
    if severity:
        query = query.where(Incident.severity == severity)
    incident_count = await session.scalar(select(func.count()).select_from(query.subquery()))
    query = apply_descending_cursor(query, Incident.last_seen, Incident.id, cursor)
    query = query.order_by(Incident.last_seen.desc(), Incident.id.desc()).limit(limit + 1)
    rows = (await session.execute(query)).scalars().all()

    from apps.control_plane.incidents.silence_service import active_silence_until

    items = []
    for incident in rows[:limit]:
        silenced_until = await active_silence_until(
            session,
            project_id=principal.project_id,
            fingerprint=incident.fingerprint,
            severity=incident.severity,
        )
        items.append(_summary(incident, silenced_until))
    next_cursor = (
        encode_cursor(rows[limit - 1].last_seen.isoformat(), str(rows[limit - 1].id))
        if len(rows) > limit
        else None
    )

    raw_alert_count = (
        await session.execute(
            select(func.count(Alert.id)).where(Alert.project_id == principal.project_id)
        )
    ).scalar_one()
    notification_count = (
        await session.execute(
            select(func.coalesce(func.sum(Incident.notification_count), 0)).where(
                Incident.project_id == principal.project_id
            )
        )
    ).scalar_one()

    return IncidentPage(
        items=items,
        next_cursor=next_cursor,
        raw_alert_count=raw_alert_count,
        incident_count=incident_count,
        notification_count=notification_count,
    )


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
async def get_incident(
    incident_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> IncidentDetail:
    incident = await _get_incident(session, incident_id, principal)
    from apps.control_plane.incidents.silence_service import active_silence_until

    silenced_until = await active_silence_until(
        session,
        project_id=principal.project_id,
        fingerprint=incident.fingerprint,
        severity=incident.severity,
    )

    alerts = (
        (
            await session.execute(
                select(Alert)
                .where(
                    Alert.fingerprint == incident.fingerprint,
                    Alert.project_id == principal.project_id,
                )
                .order_by(Alert.starts_at.asc())
            )
        )
        .scalars()
        .all()
    )

    # 时间线：告警 + 症状资源窗口事件（最近 50 条）
    timeline: list[dict] = [
        {
            "at": a.starts_at.isoformat(),
            "kind": "alert",
            "rule_id": a.rule_id,
            "state": a.state,
            "severity": a.severity,
            "source": a.source,
        }
        for a in alerts
    ]
    symptom_resource = next((a.resource_id for a in alerts if a.resource_id), None)
    if symptom_resource:
        events = (
            (
                await session.execute(
                    select(Event)
                    .where(
                        Event.resource_id == symptom_resource,
                        Event.occurred_at >= incident.first_seen,
                        Event.occurred_at <= incident.last_seen,
                    )
                    .order_by(Event.occurred_at.asc())
                    .limit(50)
                )
            )
            .scalars()
            .all()
        )
        timeline.extend(
            {
                "at": e.occurred_at.isoformat(),
                "kind": "event",
                "type": e.type,
                "event_id": str(e.id),
                "resource_id": str(e.resource_id) if e.resource_id else None,
                "task_id": (e.correlation or {}).get("task_id"),
                "severity": e.severity,
                "provenance": e.provenance,
                "source": e.source,
            }
            for e in events
        )
    timeline.sort(key=lambda item: item["at"])

    latest = (
        await session.execute(
            select(Diagnosis)
            .where(Diagnosis.incident_id == incident.id)
            .order_by(Diagnosis.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    evidence = []
    if latest is not None:
        cited = set(latest.supporting_evidence_ids) | set(latest.contradicting_evidence_ids)
        rows = (
            (
                await session.execute(
                    select(Evidence)
                    .where(Evidence.id.in_(cited), Evidence.project_id == principal.project_id)
                    .order_by(Evidence.range_start)
                )
            )
            .scalars()
            .all()
        )
        evidence = [evidence_out(row).model_dump(mode="json") for row in rows]
        cited_events = (
            (
                await session.execute(
                    select(Event).where(
                        Event.id.in_(cited), Event.project_id == principal.project_id
                    )
                )
            )
            .scalars()
            .all()
        )
        existing_ids = {item.get("event_id") for item in timeline}
        for event in cited_events:
            if str(event.id) not in existing_ids:
                timeline.append(
                    {
                        "at": event.occurred_at.isoformat(),
                        "kind": "event",
                        "type": event.type,
                        "event_id": str(event.id),
                        "resource_id": str(event.resource_id) if event.resource_id else None,
                        "task_id": (event.correlation or {}).get("task_id"),
                        "severity": event.severity,
                        "provenance": event.provenance,
                        "source": event.source,
                    }
                )
        timeline.sort(key=lambda item: item["at"])
    return IncidentDetail(
        **_summary(incident, silenced_until).model_dump(),
        alerts=[
            {
                "id": str(a.id),
                "rule_id": a.rule_id,
                "state": a.state,
                "severity": a.severity,
                "starts_at": a.starts_at.isoformat(),
                "ends_at": a.ends_at.isoformat() if a.ends_at else None,
                "source": a.source,
                "provenance": a.provenance,
            }
            for a in alerts
        ],
        timeline=timeline,
        diagnosis=_diagnosis_out(latest) if latest else None,
        evidence=evidence,
    )


@router.get("/incidents/{incident_id}/diagnoses", response_model=list[DiagnosisOut])
async def list_diagnoses(
    incident_id: uuid.UUID,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> list[DiagnosisOut]:
    incident = await _get_incident(session, incident_id, principal)
    rows = (
        (
            await session.execute(
                select(Diagnosis)
                .where(Diagnosis.incident_id == incident.id)
                .order_by(Diagnosis.revision.asc())
            )
        )
        .scalars()
        .all()
    )
    return [_diagnosis_out(r) for r in rows]


# --- 运营操作（US5）---


@router.post(
    "/incidents/{incident_id}:acknowledge",
    response_model=IncidentSummary,
    responses={"409": {"description": "当前状态不能确认"}},
)
async def acknowledge_incident(
    incident_id: uuid.UUID,
    request: AcknowledgeRequest,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> IncidentSummary:
    incident = await _get_incident(session, incident_id, principal)
    if incident.state != "open":
        raise ApiError(409, "conflict", f"状态 {incident.state} 不能确认")
    from datetime import UTC, datetime

    incident.state = "acknowledged"
    incident.acknowledged_by = request.actor
    incident.acknowledged_at = datetime.now(UTC)
    await session.flush()
    from apps.control_plane.integrations.notification import notify_transition

    await notify_transition(session, incident, "open")
    return _summary(incident)


@router.post("/silences", status_code=201, response_model=SilenceOut)
async def create_silence(
    request: SilenceRequest,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> SilenceOut:
    from apps.control_plane.incidents.silence_service import create_silence as _create

    silence = await _create(
        session,
        request=request,
        tenant_id=principal.tenant_id,
        project_id=principal.project_id,
    )
    return SilenceOut(
        id=str(silence.id),
        created_at=silence.created_at,
        matcher=request.matcher,
        reason=request.reason,
        created_by=request.created_by,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
    )
