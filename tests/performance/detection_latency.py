"""T141: measure replay detection latency through the production pipeline.

Replay compresses scenario offsets, so these measurements cover processing latency.
The operational report adds the configured sustained-threshold dwell separately.
"""

from __future__ import annotations

import math
import time

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [pytest.mark.performance]


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    rank = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[rank]


async def _truncate(session) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' AND tablename != 'alembic_version'"
            )
        )
    ).scalars().all()
    if rows:
        quoted = ", ".join(f'"{name}"' for name in sorted(rows))
        await session.execute(text(f"TRUNCATE {quoted} CASCADE"))
        await session.commit()


@pytest.mark.parametrize(
    ("kind", "scenario_name", "budget_seconds"),
    [
        ("event", "story-oom", 30.0),
        ("sustained", "story-host-pressure", 90.0),
    ],
)
async def test_detection_latency_p50_p95(
    test_database_url,
    clean_db,
    monkeypatch,
    kind: str,
    scenario_name: str,
    budget_seconds: float,
) -> None:
    from apps.control_plane.integrations import scenario_service

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    received_at = 0.0
    detected_at = 0.0
    original_ingest = scenario_service.ingestion_repository.ingest_batch
    original_pipeline = scenario_service._pipeline

    async def measured_ingest(*args, **kwargs):
        nonlocal received_at
        result = await original_ingest(*args, **kwargs)
        received_at = time.perf_counter()
        return result

    async def measured_pipeline(*args, **kwargs):
        nonlocal detected_at
        result = await original_pipeline(*args, **kwargs)
        detected_at = time.perf_counter()
        return result

    monkeypatch.setattr(scenario_service.ingestion_repository, "ingest_batch", measured_ingest)
    monkeypatch.setattr(scenario_service, "_pipeline", measured_pipeline)

    occurred_to_detection: list[float] = []
    received_to_detection: list[float] = []
    async with maker() as session:
        for iteration in range(5):
            await _truncate(session)
            received_at = 0.0
            detected_at = 0.0
            occurred_at = time.perf_counter()
            result = await scenario_service.run_scenario(
                session,
                scenario_name,
                mode="replay",
                started_by=f"latency-{kind}-{iteration}",
            )
            await session.commit()
            assert result["state"] == "succeeded"
            assert occurred_at < received_at <= detected_at
            occurred_to_detection.append(detected_at - occurred_at)
            received_to_detection.append(detected_at - received_at)

    await engine.dispose()
    metrics = {
        "kind": kind,
        "samples": len(occurred_to_detection),
        "occurred_to_detection_p50_seconds": round(
            _percentile(occurred_to_detection, 0.50), 3
        ),
        "occurred_to_detection_p95_seconds": round(
            _percentile(occurred_to_detection, 0.95), 3
        ),
        "received_to_detection_p50_seconds": round(
            _percentile(received_to_detection, 0.50), 3
        ),
        "received_to_detection_p95_seconds": round(
            _percentile(received_to_detection, 0.95), 3
        ),
    }
    print("\ndetection_latency:", metrics)
    assert metrics["occurred_to_detection_p95_seconds"] < budget_seconds
