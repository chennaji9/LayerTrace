"""US1 端到端：任务—实例—容器—VM—宿主机事实写入 → revision 1 诊断。

流程：种子拓扑（ORM）→ 事件 API 写入事实（任务失败、容器 OOM、VM 内存正常反证）
→ 运行管线（任务投影 → 中断推断 → 事件告警 → 诊断生成）→ API 读取事件单与诊断。
反例：只有退出码 137 时 R1 不成立。
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from tests.seed_utils import seed_full_stack

pytestmark = [pytest.mark.integration]

TEST_TOKEN = "test-token-us1-000000000001"


@pytest.fixture()
def us1_client(test_database_url, clean_db) -> TestClient:
    os.environ["DATABASE_URL"] = test_database_url
    os.environ["API_TOKENS"] = TEST_TOKEN
    from apps.control_plane.config import get_settings

    get_settings.cache_clear()
    from apps.control_plane.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()
    os.environ.pop("API_TOKENS", None)


@pytest.fixture()
async def seeded(test_database_url) -> dict:
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        # 固定事件时刻为 12:00/13:00 UTC，种子必须早于事件（避免关系在诊断窗口外）
        stack = await seed_full_stack(
            session,
            prefix=f"us1-{uuid.uuid4().hex[:6]}",
            now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
        )
        yield stack
    await engine.dispose()


def _auth() -> dict:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _event(event_type: str, ref: tuple[str, str], occurred: str, attrs: dict, event_id: str, **extra) -> dict:
    evt = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": event_type,
        "resource_ref": {"namespace": ref[0], "external_id": ref[1]},
        "occurred_at": occurred,
        "observed_at": occurred,
        "severity": "info",
        "source": "us1-test",
        "provenance": "observed",
        "attributes": attrs,
    }
    evt.update(extra)
    return evt


async def _run_pipeline(test_database_url) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.diagnosis.service import sweep_pending_diagnoses
    from apps.control_plane.diagnosis.task_interruption import infer_task_interruptions
    from apps.control_plane.incidents.event_alerts import process_event_alerts
    from apps.control_plane.tasks.projection import apply_task_events

    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await apply_task_events(session)
        await infer_task_interruptions(session)
        await process_event_alerts(session)
        await sweep_pending_diagnoses(session)
        await session.commit()
    await engine.dispose()


async def test_cross_layer_diagnosis_revision_1(us1_client: TestClient, seeded, test_database_url) -> None:
    stack = seeded
    refs = stack["refs"]
    base_time = "2026-09-21T12:00:00Z"

    facts = [
        # 任务开始与客户端失败观测（服务实例）
        _event(
            "task.started", ("otel.service_instance", refs["service_instance"]),
            base_time, {"phase": "model"}, "us1-task-start-1",
            correlation={"task_id": "task-us1-1"},
        ),
        _event(
            "task.failed", ("otel.service_instance", refs["service_instance"]),
            "2026-09-21T12:00:20Z", {"error_class": "instance_lost"},
            "us1-task-fail-1", correlation={"task_id": "task-us1-1"}, severity="error",
        ),
        # 容器 OOM 直接证据
        _event(
            "container.oom", ("docker.container", refs["container"]),
            "2026-09-21T12:00:18Z", {"oom_killed": True, "exit_code": 137, "memory_limit_bytes": 536870912},
            "us1-oom-1", severity="critical",
        ),
        # VM 内存正常反证
        _event(
            "vm.memory.sample", ("zsvirt.vm", refs["vm"]),
            "2026-09-21T12:00:10Z", {"available_ratio": 0.72, "used_bytes": 8589934592},
            "us1-vm-mem-1",
        ),
    ]
    res = us1_client.post(
        "/api/v1/events:batch", json={"schema_version": "1.0", "events": facts}, headers=_auth()
    )
    assert res.status_code == 202, res.text
    assert res.json()["accepted"] == len(facts)

    await _run_pipeline(test_database_url)

    # 仅形成 1 个聚合事件单
    incidents = us1_client.get("/api/v1/incidents", headers=_auth()).json()
    assert incidents["incident_count"] >= 1
    target = None
    for item in incidents["items"]:
        if item["alert_count"] >= 1 and item["state"] in ("open", "acknowledged"):
            target = item
    assert target, f"应存在打开的事件单：{incidents}"

    # 事件单详情：告警、时间线与诊断
    detail = us1_client.get(f"/api/v1/incidents/{target['id']}", headers=_auth()).json()
    assert detail["alerts"], "事件单应包含告警"
    assert detail["timeline"], "事件单应包含时间线"
    assert detail["diagnosis"] is not None

    # 诊断修订：revision 1、证据数组、反证、缺失项、规则版本
    diagnoses = us1_client.get(
        f"/api/v1/incidents/{target['id']}/diagnoses", headers=_auth()
    ).json()
    assert isinstance(diagnoses, list) and diagnoses
    first = diagnoses[0]
    assert first["revision"] == 1
    for field in (
        "supporting_evidence_ids", "contradicting_evidence_ids", "missing_evidence",
        "observed_impact", "potential_impact", "rule_versions",
    ):
        assert field in first, f"诊断缺字段 {field}"
    assert first["rule_versions"].get("R1")
    candidates = first["candidates"]
    assert candidates, "诊断必须包含候选"
    r1 = next((c for c in candidates if c["rule_id"] == "R1"), None)
    assert r1, f"必须包含 R1 候选：{[c['rule_id'] for c in candidates]}"
    assert r1["support_level"] == "high"
    assert r1["supporting"], "R1 候选必须有支持证据"
    assert r1["recommendations"], "R1 候选必须有处置建议"
    # 反证：VM 内存正常
    assert first["contradicting_evidence_ids"], "诊断必须包含 VM 内存正常反证"


async def test_exit_137_without_oom_not_diagnosed(us1_client: TestClient, seeded, test_database_url) -> None:
    stack = seeded
    refs = stack["refs"]
    facts = [
        _event(
            "task.failed", ("otel.service_instance", refs["service_instance"]),
            "2026-09-21T13:00:20Z", {"error_class": "client_error"},
            "us1b-task-fail-1", correlation={"task_id": "task-us1b-1"}, severity="error",
        ),
        _event(
            "container.exit", ("docker.container", refs["container"]),
            "2026-09-21T13:00:18Z", {"exit_code": 137},
            "us1b-exit-1", severity="warning",
        ),
    ]
    res = us1_client.post(
        "/api/v1/events:batch", json={"schema_version": "1.0", "events": facts}, headers=_auth()
    )
    assert res.status_code == 202

    await _run_pipeline(test_database_url)

    incidents = us1_client.get("/api/v1/incidents?state=open", headers=_auth()).json()
    r1_high = []
    for item in incidents["items"]:
        diagnoses = us1_client.get(
            f"/api/v1/incidents/{item['id']}/diagnoses", headers=_auth()
        ).json()
        for diag in diagnoses:
            for cand in diag["candidates"]:
                if cand["rule_id"] == "R1" and cand["support_level"] in ("high",):
                    r1_high.append(cand)
    assert not r1_high, f"仅退出码 137 不得成立 R1 高支持：{r1_high}"
