"""任务中断推断（T044）。

实例（服务实例/容器）退出时，同实例上仍在 running 的任务标记为
`interrupted/inferred` 并引用触发事件；客户端观测迟到时只追加证据，
不覆盖推断来源。
"""

from __future__ import annotations

import logging

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Event, Relationship, TaskRun

logger = logging.getLogger(__name__)

# 视为实例退出的证据事件类型
INSTANCE_EXIT_TYPES = ("container.exit", "container.oom", "service_instance.stopped")


async def infer_task_interruptions(session: AsyncSession, exits: list[Event] | None = None) -> int:
    """扫描退出事件，把受影响的 running 任务推断为 interrupted。幂等。"""
    inferred = 0
    if exits is None:
        exits = (
            (
                await session.execute(
                    select(Event)
                    .where(Event.type.in_(INSTANCE_EXIT_TYPES))
                    .order_by(Event.occurred_at.asc())
                )
            )
            .scalars()
            .all()
        )
    for event in exits:
        if event.type not in INSTANCE_EXIT_TYPES:
            continue
        if not event.resource_id:
            continue
        exit_at = event.occurred_at

        # 找到退出容器上有效的服务实例（container runs service_instance）
        instances = (
            (
                await session.execute(
                    select(Relationship.to_id).where(
                        Relationship.from_id == event.resource_id,
                        Relationship.type == "runs",
                        Relationship.valid_from <= exit_at,
                        or_(Relationship.valid_to.is_(None), Relationship.valid_to > exit_at),
                    )
                )
            )
            .scalars()
            .all()
        )
        instance_ids = [event.resource_id, *instances]

        running = (
            (
                await session.execute(
                    select(TaskRun).where(
                        TaskRun.service_instance_id.in_(instance_ids),
                        TaskRun.project_id == event.project_id,
                        TaskRun.status == "running",
                        TaskRun.started_at <= exit_at,
                    )
                )
            )
            .scalars()
            .all()
        )
        for task in running:
            task.status = "interrupted"
            task.status_provenance = "inferred"
            task.ended_at = exit_at
            task.trigger_event_id = event.id
            inferred += 1

    if inferred:
        await session.flush()
        logger.info("推断 %d 个任务为 interrupted", inferred)
    return inferred
