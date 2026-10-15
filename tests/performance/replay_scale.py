"""T140：回放规模压测（10/50/100 台等价）。

声明：回放压测不替代真实网络/探针规模测试（docs/capacity-report.md）。
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import func, select

pytestmark = [pytest.mark.performance]


def _scaled_batch(vms: int, tag: str) -> list:
    from packages.contracts.schemas import EventInput

    events = []
    for vm in range(vms):
        for i in range(5):  # 每台 VM 5 条事件
            events.append(EventInput(
                schema_version="1.0",
                event_id=f"{tag}-v{vm}-e{i}",
                event_type="container.stats.sample",
                resource_ref={"namespace": "zsvirt.vm", "external_id": f"{tag}-vm-{vm}"},
                occurred_at=f"2026-09-21T21:{i:02d}:00Z",
                observed_at=f"2026-09-21T21:{i:02d}:00Z",
                source="scale-test", provenance="observed",
                attributes={"cpu_percent": 42.0},
            ))
    return events


@pytest.mark.parametrize("vms", [10, 50, 100])
async def test_replay_scale_10_50_100(test_database_url, clean_db, vms: int) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.ingestion import repository as ingestion_repo
    from apps.control_plane.integrations.scenario_service import run_scenario
    from apps.control_plane.models import Event
    from packages.contracts.schemas import EventBatch

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        tag = uuid.uuid4().hex[:6]
        events = _scaled_batch(vms, tag)
        ingest_started = time.monotonic()
        result = await ingestion_repo.ingest_batch(
            session,
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            batch=EventBatch(schema_version="1.0", events=events),
        )
        await session.commit()
        ingest_elapsed = time.monotonic() - ingest_started

        persisted = (
            await session.execute(
                select(func.count()).select_from(Event).where(Event.source == "scale-test")
            )
        ).scalar_one()
        queue_backlog = len(events) - persisted

        diagnosis_started = time.monotonic()
        scenario = await run_scenario(
            session,
            "story-oom",
            mode="replay",
            started_by=f"scale-test-{vms}",
        )
        await session.commit()
        diagnosis_elapsed = time.monotonic() - diagnosis_started

        assert result.accepted == len(events)
        assert persisted == len(events)
        assert queue_backlog == 0
        assert scenario["state"] == "succeeded"

        metrics = {
            "vms": vms,
            "samples_per_vm": len(events) // vms,
            "events": len(events),
            "ingest_seconds": round(ingest_elapsed, 3),
            "events_per_second": round(len(events) / ingest_elapsed, 1),
            "queue_backlog_after_commit": queue_backlog,
            "diagnosis_pipeline_seconds": round(diagnosis_elapsed, 3),
        }
        print("\nreplay_scale:", metrics)
    await engine.dispose()
