"""任务事件投影：task.* 事件 → TaskRun 表。

- task.started 在执行前写入（进入 spool 的语义由探针保证）；
- 终态事件只更新仍处于 running 的任务；
- 迟到的客户端观测到达时若任务已是终态（如推断中断），不覆盖原状态与来源，
  只保留事件本身作为补强证据（data-model 2.6）。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Event, TaskRun

logger = logging.getLogger(__name__)

TASK_EVENT_TYPES = {
    "task.started": "running",
    "task.succeeded": "succeeded",
    "task.failed": "failed",
    "task.timed_out": "timed_out",
}


async def apply_task_events(session: AsyncSession, events: list[Event] | None = None) -> int:
    """把尚未投影的 task.* 事件应用到 TaskRun。幂等。"""
    projected_any = 0
    if events is None:
        result = await session.execute(
            select(Event)
            .where(Event.type.in_(TASK_EVENT_TYPES.keys()))
            .order_by(Event.occurred_at.asc(), Event.observed_at.asc())
        )
        events = result.scalars().all()
    for event in events:
        if event.type not in TASK_EVENT_TYPES:
            continue
        if not event.resource_id:
            continue  # 身份未解析，等待补齐后重算（T066）
        task_id = (event.correlation or {}).get("task_id")
        if not task_id:
            continue
        payload = event.payload or {}
        existing = (
            await session.execute(
                select(TaskRun).where(
                    TaskRun.project_id == event.project_id,
                    TaskRun.task_id == task_id,
                )
            )
        ).scalar_one_or_none()

        target_status = TASK_EVENT_TYPES[event.type]
        if existing is None:
            if event.type != "task.started":
                # 只有结束事件：仍创建任务记录，状态按事件，开始时间未知取发生时间
                session.add(
                    TaskRun(
                        tenant_id=event.tenant_id,
                        project_id=event.project_id,
                        task_id=task_id,
                        service_instance_id=event.resource_id,
                        trace_id=(event.correlation or {}).get("trace_id"),
                        started_at=event.occurred_at,
                        ended_at=event.occurred_at if target_status != "running" else None,
                        status=target_status,
                        status_provenance=event.provenance,
                        error_class=payload.get("error_class"),
                        duration_ms=payload.get("duration_ms"),
                        model_duration_ms=payload.get("model_duration_ms"),
                        tool_duration_ms=payload.get("tool_duration_ms"),
                    )
                )
                projected_any += 1
            else:
                session.add(
                    TaskRun(
                        tenant_id=event.tenant_id,
                        project_id=event.project_id,
                        task_id=task_id,
                        service_instance_id=event.resource_id,
                        trace_id=(event.correlation or {}).get("trace_id"),
                        started_at=event.occurred_at,
                        status="running",
                        status_provenance=event.provenance,
                    )
                )
                projected_any += 1
            continue

        if existing.service_instance_id != event.resource_id:
            continue  # the same task ID cannot transfer to an unrelated generation
        if event.type == "task.started" and event.occurred_at < existing.started_at:
            existing.started_at = event.occurred_at
        if existing.status != "running":
            # 已终态：迟到观测不覆盖（仅追加证据），跳过
            continue

        if event.type == "task.started":
            continue  # 重复开始事件：幂等跳过

        existing.status = target_status
        existing.status_provenance = event.provenance
        existing.ended_at = max(existing.started_at, event.occurred_at)
        existing.error_class = payload.get("error_class")
        for field in ("duration_ms", "model_duration_ms", "tool_duration_ms"):
            if payload.get(field) is not None:
                setattr(existing, field, payload[field])
        projected_any += 1

    if projected_any:
        await session.flush()
    return projected_any
