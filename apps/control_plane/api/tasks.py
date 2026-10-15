"""任务查询 API（T086）：TaskRun 查询、状态来源和分段耗时。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.api.pagination import apply_descending_cursor, encode_cursor
from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.db import get_session
from apps.control_plane.models import Diagnosis, Event, Incident, TaskRun
from packages.contracts.schemas import TaskRunOut, TaskRunPage

router = APIRouter(prefix="/api/v1", tags=["Tasks"])


@router.get("/tasks", response_model=TaskRunPage)
async def list_task_runs(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    task_id: str | None = Query(default=None),
    service_instance_id: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TaskRunPage:
    query = select(TaskRun).where(TaskRun.project_id == principal.project_id)
    if status:
        query = query.where(TaskRun.status == status)
    if task_id:
        query = query.where(TaskRun.task_id == task_id)
    if service_instance_id:
        query = query.where(TaskRun.service_instance_id == service_instance_id)
    query = apply_descending_cursor(query, TaskRun.started_at, TaskRun.id, cursor)
    query = query.order_by(TaskRun.started_at.desc(), TaskRun.id.desc()).limit(limit + 1)

    rows = (await session.execute(query)).scalars().all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].started_at.isoformat(), str(page[-1].id))
        if len(rows) > limit and page
        else None
    )
    return TaskRunPage(
        items=[
            TaskRunOut(
                id=str(r.id),
                task_id=r.task_id,
                service_instance_id=str(r.service_instance_id),
                trace_id=r.trace_id,
                started_at=r.started_at,
                ended_at=r.ended_at,
                status=r.status,  # type: ignore[arg-type]
                status_provenance=r.status_provenance,  # type: ignore[arg-type]
                error_class=r.error_class,
                duration_ms=r.duration_ms,
                model_duration_ms=r.model_duration_ms,
                tool_duration_ms=r.tool_duration_ms,
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )


@router.get("/tasks/{task_id}")
async def task_detail(
    task_id: str,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    task = (
        await session.execute(
            select(TaskRun).where(
                TaskRun.project_id == principal.project_id, TaskRun.task_id == task_id
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise ApiError(404, "not_found", "任务不存在")
    events = (
        (
            await session.execute(
                select(Event)
                .where(
                    Event.project_id == principal.project_id,
                    Event.correlation["task_id"].astext == task_id,
                )
                .order_by(Event.occurred_at)
            )
        )
        .scalars()
        .all()
    )
    diagnoses = (
        (
            await session.execute(
                select(Diagnosis)
                .join(Incident, Incident.id == Diagnosis.incident_id)
                .where(Incident.project_id == principal.project_id)
            )
        )
        .scalars()
        .all()
    )
    incident_ids = sorted(
        {
            str(diagnosis.incident_id)
            for diagnosis in diagnoses
            if task_id in (diagnosis.observed_impact or {}).get("task_ids", [])
        }
    )
    return {
        "task": TaskRunOut(
            id=str(task.id),
            task_id=task.task_id,
            service_instance_id=str(task.service_instance_id),
            trace_id=task.trace_id,
            started_at=task.started_at,
            ended_at=task.ended_at,
            status=task.status,
            status_provenance=task.status_provenance,
            error_class=task.error_class,
            duration_ms=task.duration_ms,
            model_duration_ms=task.model_duration_ms,
            tool_duration_ms=task.tool_duration_ms,
        ).model_dump(mode="json"),
        "events": [
            {
                "id": str(event.id),
                "type": event.type,
                "resource_id": str(event.resource_id) if event.resource_id else None,
                "at": event.occurred_at.isoformat(),
                "source": event.source,
                "provenance": event.provenance,
                "attributes": event.payload,
            }
            for event in events
        ],
        "incident_ids": incident_ids,
    }
