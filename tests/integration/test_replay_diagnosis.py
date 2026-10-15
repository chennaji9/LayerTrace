"""US2 集成测试：清库重跑一致性、同管线入库、137 非 OOM 反例。"""

from __future__ import annotations

import psycopg
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.integrations.scenario_service import (
    run_scenario,
    verify_scenario,
)
from apps.control_plane.models import Diagnosis, Event, Relationship, Resource

pytestmark = [pytest.mark.integration]


async def _session_for(test_database_url: str):
    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    return engine, maker


async def _run_scenario(test_database_url: str, name: str) -> dict:
    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        result = await run_scenario(session, name, mode="replay", started_by="integration-test")
        await session.commit()
    await engine.dispose()
    return result


async def _verify(test_database_url: str, run_id: str) -> dict:
    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        result = await verify_scenario(session, run_id)
        await session.commit()
    await engine.dispose()
    return result


async def _r1_high_count(test_database_url: str) -> int:
    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        rows = (await session.execute(select(Diagnosis))).scalars().all()
        count = sum(
            1
            for d in rows
            for c in d.candidates
            if c.get("rule_id") == "R1" and c.get("support_level") == "high"
        )
    await engine.dispose()
    return count


async def _signature(test_database_url: str) -> list:
    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        rows = (await session.execute(select(Diagnosis))).scalars().all()
        sig = sorted(
            (
                c.get("rule_id"),
                c.get("support_level"),
                len(c.get("supporting", [])),
                len(c.get("contradicting", [])),
            )
            for d in rows
            for c in d.candidates
        )
    await engine.dispose()
    return sig


def _wipe() -> None:
    from tests.conftest import SYNC_TEST_URL

    with psycopg.connect(SYNC_TEST_URL) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname='public' AND tablename != 'alembic_version'
                """
            )
            tables = [r[0] for r in cur.fetchall()]
            if tables:
                cur.execute(f"TRUNCATE {', '.join(sorted(tables))} CASCADE")
        conn.commit()


async def test_story_oom_replay_end_to_end(test_database_url, clean_db) -> None:
    run = await _run_scenario(test_database_url, "story-oom")
    assert run["state"] == "succeeded", run
    verification = await _verify(test_database_url, run["id"])
    assert verification["state"] == "succeeded"
    assert verification["verification_result"]["passed"], verification
    assert await _r1_high_count(test_database_url) >= 1


async def test_repeated_diagnosis_sweep_without_new_evidence_is_idempotent(
    test_database_url, clean_db
) -> None:
    """Future-offset replay timestamps must not create duplicate revisions."""
    from apps.control_plane.diagnosis.service import sweep_pending_diagnoses

    await _run_scenario(test_database_url, "story-oom")
    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        assert await sweep_pending_diagnoses(session) == 0
        assert await sweep_pending_diagnoses(session) == 0
        revisions = (await session.execute(select(Diagnosis.revision))).scalars().all()
    await engine.dispose()

    assert revisions == [1]


async def test_clean_db_rerun_consistency(test_database_url) -> None:
    """清库重跑：关键诊断结果保持一致。"""
    _wipe()
    run1 = await _run_scenario(test_database_url, "story-oom")
    v1 = await _verify(test_database_url, run1["id"])
    signature1 = await _signature(test_database_url)

    _wipe()
    run2 = await _run_scenario(test_database_url, "story-oom")
    v2 = await _verify(test_database_url, run2["id"])
    signature2 = await _signature(test_database_url)

    assert v1["verification_result"]["passed"]
    assert v2["verification_result"]["passed"]
    assert signature1 == signature2, "清库重跑后关键诊断结果不一致"


async def test_story_exit_137_not_oom(test_database_url, clean_db) -> None:
    run = await _run_scenario(test_database_url, "story-exit-137-not-oom")
    verification = await _verify(test_database_url, run["id"])
    assert verification["verification_result"]["passed"]
    assert await _r1_high_count(test_database_url) == 0


async def test_counterexample_verification_isolated_after_positive(
    test_database_url, clean_db
) -> None:
    """A prior positive scenario must not contaminate counterexample verification."""
    positive = await _run_scenario(test_database_url, "story-oom")
    assert (await _verify(test_database_url, positive["id"]))["verification_result"][
        "passed"
    ]

    counterexample = await _run_scenario(test_database_url, "story-exit-137-not-oom")
    verification = await _verify(test_database_url, counterexample["id"])
    assert verification["verification_result"]["passed"], verification
    assert verification["verification_result"]["context_summary"]["r1_high_count"] == 0


async def test_replay_writes_through_same_pipeline(test_database_url, clean_db) -> None:
    """回放事实经正式管线入库：资源、别名、关系、事件均带 replayed 来源。"""
    await _run_scenario(test_database_url, "story-oom")

    engine, maker = await _session_for(test_database_url)
    async with maker() as session:
        resources = (await session.execute(select(Resource))).scalars().all()
        rels = (await session.execute(select(Relationship))).scalars().all()
        events = (await session.execute(select(Event))).scalars().all()
    await engine.dispose()

    assert resources and all(r.provenance == "replayed" for r in resources)
    assert rels and all(r.provenance == "replayed" for r in rels)
    assert events
    assert all(e.provenance == "replayed" for e in events)
    assert all(e.scenario_name == "story-oom" for e in events)
    # 期望断言不得进入产品表
    for e in events:
        assert "expected" not in str(e.payload).lower()
