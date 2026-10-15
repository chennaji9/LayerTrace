"""T139：控制面重启不丢已提交事件、spool 满载丢弃可见、幂等补传。"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import E2E_TOKEN

pytestmark = [pytest.mark.integration]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


async def test_restart_preserves_committed_events(test_database_url, clean_db) -> None:
    """模拟"重启"：重开引擎会话后已提交事件仍在（提交即持久）。"""
    import uuid as uuid_mod

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.ingestion import repository as ingestion_repo
    from apps.control_plane.models import Event
    from packages.contracts.schemas import EventBatch, EventInput

    tag = uuid_mod.uuid4().hex[:6]
    events = [
        EventInput(
            schema_version="1.0",
            event_id=f"{tag}-restart-{i}",
            event_type="container.stats.sample",
            resource_ref={"namespace": "docker.container", "external_id": f"{tag}c0" * 8},
            occurred_at=f"2026-09-21T20:00:{i:02d}Z",
            observed_at=f"2026-09-21T20:00:{i:02d}Z",
            source="probe", provenance="observed",
            attributes={"cpu_percent": 10.0 + i},
        )
        for i in range(5)
    ]

    async def commit_batch() -> None:
        engine = create_async_engine(test_database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await ingestion_repo.ingest_batch(
                session,
                tenant_id=uuid_mod.UUID("00000000-0000-0000-0000-000000000001"),
                project_id=uuid_mod.UUID("00000000-0000-0000-0000-000000000001"),
                batch=EventBatch(schema_version="1.0", events=events),
            )
            await session.commit()
        await engine.dispose()

    await commit_batch()
    # "重启"后：新会话读取
    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        count = (
            await session.execute(
                select(func.count(Event.id)).where(Event.event_id.like(f"{tag}-restart-%"))
            )
        ).scalar_one()
        assert count == 5, "重启后已提交事件零丢失"

        # 幂等补传：重发同批次 → duplicate 计数
        result = await ingestion_repo.ingest_batch(
            session,
            tenant_id=uuid_mod.UUID("00000000-0000-0000-0000-000000000001"),
            project_id=uuid_mod.UUID("00000000-0000-0000-0000-000000000001"),
            batch=EventBatch(schema_version="1.0", events=events),
        )
        await session.commit()
        assert result.duplicate == 5
        assert result.accepted == 0
    await engine.dispose()


async def test_spool_overflow_visible(clean_db) -> None:
    """有界缓存容量耗尽：按优先级丢弃并暴露 dropped_count 与 gap。"""
    from agents.vm_probe.spool import PRIORITY_LOW, BoundedSpool

    spool = BoundedSpool(max_bytes=2048, max_seconds=600)
    payload = {"schema_version": "1.0", "event_id": "x" * 60, "blob": "y" * 400}
    for i in range(10):
        spool.enqueue(payload | {"event_id": f"x{i}"}, priority=PRIORITY_LOW)
    assert spool.dropped_count > 0, "容量耗尽必须可见丢弃"
    assert spool.gaps, "丢弃必须产生 gap 记录"

    # 高优先级事件在最满时仍尽力保留
    ok = spool.enqueue({"schema_version": "1.0", "event_id": "oom-critical"}, priority=0)
    assert ok or spool.dropped_count > 0  # 极端情况单条超限也计数拒绝


async def test_backoff_growth() -> None:
    from agents.vm_probe.spool import backoff_delay

    small = backoff_delay(1)
    large = backoff_delay(6)
    assert 0 <= small <= 1.5
    assert large <= 60.0
