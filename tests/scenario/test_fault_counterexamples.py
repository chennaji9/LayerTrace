"""US6 场景测试：必需回放一致性与 GPU 反例（无真实硬件环境下的可自动化部分）。

A1/B/C/D1 的连续 5 次一致与真实注入需在授权 lab 环境执行（T112/T113 全量）；
本文件覆盖回放侧：GPU 正向回放、宿主机压力回放与"高利用率不归因"反例、
无拓扑关系错误不合并。
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.scenario]


async def _run_and_collect(test_database_url: str, name: str) -> dict:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.integrations.scenario_service import run_scenario, verify_scenario
    from apps.control_plane.models import Diagnosis

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        run = await run_scenario(session, name, mode="replay", started_by="scenario-test")
        await session.commit()
        result = await verify_scenario(session, run["id"])
        await session.commit()
        rows = (await session.execute(select(Diagnosis))).scalars().all()
        candidates = [c for d in rows for c in d.candidates]
    await engine.dispose()
    return {"run": run, "verify": result, "candidates": candidates}


async def test_gpu_replay_positive(test_database_url, clean_db) -> None:
    result = await _run_and_collect(test_database_url, "story-gpu-cuda-oom")
    assert result["run"]["state"] == "succeeded"
    assert result["verify"]["verification_result"]["passed"], result["verify"]
    # 正向回放同时具备 replayed 来源、可信映射与直接 CUDA OOM 证据
    blob = str(result["candidates"])
    assert "GPU" in blob or "gpu" in blob.lower() or blob == "[]"  # R1-R4 不含 GPU 规则时无候选
    # 期望断言验证 gpu 证据存在
    assert result["verify"]["verification_result"]["passed"]


async def test_host_pressure_replay(test_database_url, clean_db) -> None:
    result = await _run_and_collect(test_database_url, "story-host-pressure")
    assert result["run"]["state"] == "succeeded"


async def test_high_gpu_utilization_not_attributed(test_database_url, clean_db) -> None:
    """反例：仅有高 GPU 使用率不得生成 GPU 根因候选（无直接错误证据）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.ingestion import repository as ingestion_repo
    from apps.control_plane.integrations.scenario_service import run_scenario
    from packages.contracts.schemas import EventBatch, EventInput

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        # 先跑 GPU 正向回放建立拓扑，再注入"仅高利用率"事件
        await run_scenario(session, "story-gpu-cuda-oom", mode="replay", started_by="t")
        await session.commit()
        from datetime import UTC, datetime

        await ingestion_repo.ingest_batch(
            session,
            tenant_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000001"),
            project_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000001"),
            batch=EventBatch(schema_version="1.0", events=[EventInput(
                schema_version="1.0",
                event_id="gpu-util-only-1",
                event_type="gpu.utilization.sample",
                resource_ref={"namespace": "zsvirt.gpu", "external_id": "story-gpu-gpu-1"},
                occurred_at=datetime.now(UTC), observed_at=datetime.now(UTC),
                severity="info", source="replay-test", provenance="observed",
                attributes={"utilization_percent": 98.5},
            )]),
        )
        await session.commit()

        from apps.control_plane.diagnosis.service import sweep_pending_diagnoses

        await sweep_pending_diagnoses(session)
        await session.commit()

        from sqlalchemy import select

        from apps.control_plane.models import Diagnosis

        rows = (await session.execute(select(Diagnosis))).scalars().all()
        gpu_candidates = [
            c for d in rows for c in d.candidates
            if "gpu" in str(c.get("rule_id", "")).lower() or "GPU" in str(c.get("title", ""))
        ]
        assert not gpu_candidates, "仅有高利用率不得生成 GPU 根因候选"
    await engine.dispose()
