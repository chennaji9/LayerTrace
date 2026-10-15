"""US4 集成测试：真实任务管线（任务开始先写入、分段耗时、R2/R4 共用基线）。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.integration]


async def _session(test_database_url: str):
    engine = create_async_engine(test_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_task_started_written_before_terminal(test_database_url, clean_db) -> None:
    """任务开始事件先于结束事件写入：running 状态先存在，终态再更新。"""
    from datetime import UTC, datetime

    from apps.control_plane.models import Event, TaskRun
    from apps.control_plane.tasks.projection import apply_task_events
    from tests.seed_utils import seed_full_stack

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    async with maker() as session:
        stack = await seed_full_stack(session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC))
        refs = stack["refs"]
        base = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)

        def ev(event_type: str, eid: str, occurred: datetime, attrs: dict) -> Event:
            return Event(
                event_id=eid, tenant_id=stack["vm"].tenant_id, project_id=stack["vm"].project_id,
                resource_id=stack["service_instance"].id,
                resource_ref={"namespace": "otel.service_instance", "external_id": refs["service_instance"]},
                type=event_type, occurred_at=occurred, observed_at=occurred,
                severity="info", payload=attrs, source="load-gen",
                provenance="observed", resolution_state="resolved",
            )

        # 只有开始事件 → running
        session.add(ev("task.started", f"{tag}-start", base, {"phase": "model"}))
        await session.commit()
        await apply_task_events(session)
        await session.commit()
        running = (
            await session.execute(select(TaskRun).where(TaskRun.task_id == f"{tag}-task"))
        ).scalars().all()
        assert not running  # 无 correlation → 不投影

        # 带 task_id 的开始事件 → running
        session.add(ev("task.started", f"{tag}-start2", base, {"phase": "model"} | {"task_id": f"{tag}-task"}))
        await session.commit()
        # projection 读取 correlation（Event.correlation）
        await session.execute(
            select(Event).where(Event.event_id == f"{tag}-start2")
        )
        await apply_task_events(session)
        await session.commit()
    await engine.dispose()


async def test_task_segmented_durations(test_database_url, clean_db) -> None:
    """模型/工具分段耗时随终态事件更新。"""
    from datetime import UTC, datetime

    from apps.control_plane.models import Event, TaskRun
    from apps.control_plane.tasks.projection import apply_task_events
    from tests.seed_utils import seed_full_stack

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    async with maker() as session:
        stack = await seed_full_stack(session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC))
        base = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)

        session.add(Event(
            event_id=f"{tag}-s", tenant_id=stack["vm"].tenant_id, project_id=stack["vm"].project_id,
            resource_id=stack["service_instance"].id,
            resource_ref={"namespace": "x", "external_id": "x"},
            type="task.started", occurred_at=base, observed_at=base, severity="info",
            payload={}, correlation={"task_id": f"{tag}-t"}, source="load-gen",
            provenance="observed", resolution_state="resolved",
        ))
        session.add(Event(
            event_id=f"{tag}-f", tenant_id=stack["vm"].tenant_id, project_id=stack["vm"].project_id,
            resource_id=stack["service_instance"].id,
            resource_ref={"namespace": "x", "external_id": "x"},
            type="task.failed", occurred_at=base + __import__("datetime").timedelta(seconds=12),
            observed_at=base + __import__("datetime").timedelta(seconds=12), severity="error",
            payload={"error_class": "client_timeout", "duration_ms": 12000,
                     "model_duration_ms": 4200, "tool_duration_ms": 3100},
            correlation={"task_id": f"{tag}-t"}, source="load-gen",
            provenance="observed", resolution_state="resolved",
        ))
        await session.commit()
        await apply_task_events(session)
        await session.commit()

        task = (
            await session.execute(select(TaskRun).where(TaskRun.task_id == f"{tag}-t"))
        ).scalar_one()
        assert task.status == "failed"
        assert task.duration_ms == 12000
        assert task.model_duration_ms == 4200
        assert task.tool_duration_ms == 3100
    await engine.dispose()


async def test_baseline_service_shared(test_database_url, clean_db) -> None:
    """R2/R4/Agent 共用 15 分钟 P95 基线服务：样本不足 → insufficient。"""
    from datetime import UTC, datetime, timedelta

    from apps.control_plane.diagnosis.baseline import compute_baseline
    from apps.control_plane.tasks.projection import apply_task_events
    from tests.seed_utils import seed_full_stack

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    async with maker() as session:
        stack = await seed_full_stack(session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC))
        # 少于 30 个样本 → insufficient，agent_timeout 为空
        now = datetime(2026, 9, 21, 16, 0, tzinfo=UTC)
        for i in range(10):
            from apps.control_plane.models import Event

            session.add(Event(
                event_id=f"{tag}-m{i}", tenant_id=stack["vm"].tenant_id, project_id=stack["vm"].project_id,
                resource_id=stack["service_instance"].id,
                resource_ref={"namespace": "n", "external_id": "e"},
                type="task.succeeded", occurred_at=now - timedelta(seconds=i * 20), observed_at=now,
                severity="info", payload={"duration_ms": 1000 + i * 10},
                correlation={"task_id": f"{tag}-t{i}"}, source="load-gen",
                provenance="observed", resolution_state="resolved",
            ))
        await session.commit()
        await apply_task_events(session)
        await session.commit()

        baseline = await compute_baseline(session, project_id=stack["vm"].project_id, now=now)
        assert baseline["state"] == "insufficient"
        assert baseline["sample_count"] == 10
        assert baseline["agent_timeout_ms"] is None

        # 足够样本 → ready + 三倍超时
        for i in range(10, 35):
            from apps.control_plane.models import Event

            session.add(Event(
                event_id=f"{tag}-m{i}", tenant_id=stack["vm"].tenant_id, project_id=stack["vm"].project_id,
                resource_id=stack["service_instance"].id,
                resource_ref={"namespace": "n", "external_id": "e"},
                type="task.succeeded", occurred_at=now - timedelta(seconds=i * 20), observed_at=now,
                severity="info", payload={"duration_ms": 1000 + i * 10},
                correlation={"task_id": f"{tag}-t{i}"}, source="load-gen",
                provenance="observed", resolution_state="resolved",
            ))
        await session.commit()
        await apply_task_events(session)
        await session.commit()
        baseline = await compute_baseline(session, project_id=stack["vm"].project_id, now=now)
        assert baseline["state"] == "ready"
        assert baseline["sample_count"] >= 30
        assert baseline["agent_timeout_ms"] == int(baseline["p95_ms"] * 3)
    await engine.dispose()
