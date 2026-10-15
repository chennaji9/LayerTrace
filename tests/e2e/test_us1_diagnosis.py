"""US1 E2E：用户从事件单进入诊断页并看到可追溯 R1 结论与 137 反例。

注意：使用同步 Playwright API，测试函数必须是同步 def（不能在 asyncio 循环内）。
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN, run_async

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


@pytest.fixture()
def e2e_env(test_database_url, clean_db, live_server):
    return live_server


def _post_events(base: str, events: list[dict]) -> None:
    res = httpx.post(
        f"{base}/api/v1/events:batch",
        json={"schema_version": "1.0", "events": events},
        headers=AUTH,
        timeout=10,
    )
    assert res.status_code == 202, res.text


def _run_pipeline(test_database_url: str) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.diagnosis.service import sweep_pending_diagnoses
    from apps.control_plane.diagnosis.task_interruption import infer_task_interruptions
    from apps.control_plane.incidents.event_alerts import process_event_alerts
    from apps.control_plane.tasks.projection import apply_task_events

    async def _pipeline() -> None:
        engine = create_async_engine(test_database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await apply_task_events(session)
            await infer_task_interruptions(session)
            await process_event_alerts(session)
            await sweep_pending_diagnoses(session)
            await session.commit()
        await engine.dispose()

    run_async(_pipeline())


def _event(event_type: str, ref: tuple[str, str], occurred: str, attrs: dict, event_id: str, **extra) -> dict:
    evt = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": event_type,
        "resource_ref": {"namespace": ref[0], "external_id": ref[1]},
        "occurred_at": occurred,
        "observed_at": occurred,
        "severity": "info",
        "source": "e2e-us1",
        "provenance": "observed",
        "attributes": attrs,
    }
    evt.update(extra)
    return evt


def _seed_stack(test_database_url: str, prefix: str) -> dict:
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.seed_utils import seed_full_stack

    async def _do() -> dict:
        engine = create_async_engine(test_database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            stack = await seed_full_stack(
                session,
                prefix=prefix,
                now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
            )
            await session.commit()
        await engine.dispose()
        return stack

    return run_async(_do())


def test_us1_diagnosis_page_shows_r1(desktop_page, origin_assert, e2e_env, test_database_url) -> None:
    base = e2e_env
    stack = _seed_stack(test_database_url, f"e2eoom{uuid.uuid4().hex[:6]}")
    refs = stack["refs"]

    facts = [
        _event(
            "task.failed", ("otel.service_instance", refs["service_instance"]),
            "2026-09-21T14:00:20Z", {"error_class": "instance_lost"},
            f"e2e-oom-{uuid.uuid4().hex[:6]}-task", correlation={"task_id": "e2e-task-1"}, severity="error",
        ),
        _event(
            "container.oom", ("docker.container", refs["container"]),
            "2026-09-21T14:00:18Z", {"oom_killed": True, "exit_code": 137},
            f"e2e-oom-{uuid.uuid4().hex[:6]}-oom", severity="critical",
        ),
        _event(
            "vm.memory.sample", ("zsvirt.vm", refs["vm"]),
            "2026-09-21T14:00:10Z", {"available_ratio": 0.75},
            f"e2e-oom-{uuid.uuid4().hex[:6]}-vm",
        ),
    ]
    _post_events(base, facts)
    _run_pipeline(test_database_url)

    desktop_page.goto(f"{base}/pages/diagnosis")
    desktop_page.wait_for_selector("#incident-table tbody tr button", timeout=15000)
    desktop_page.click("#incident-table tbody tr:first-child button")
    desktop_page.wait_for_selector(".candidate", timeout=15000)

    body = desktop_page.inner_text("#d-candidates")
    assert "容器内存不足" in body
    assert "high" in body
    meta = desktop_page.inner_text("#d-meta")
    assert "R1" in meta
    assert "建议" in body

    assert any("/api/v1/incidents" in p for p in origin_assert.data_requests)


def test_us1_exit137_counterexample(mobile_page, e2e_env, test_database_url) -> None:
    base = e2e_env
    stack = _seed_stack(test_database_url, f"e2e137{uuid.uuid4().hex[:6]}")
    refs = stack["refs"]

    _post_events(
        base,
        [
            _event(
                "container.exit", ("docker.container", refs["container"]),
                "2026-09-21T15:00:18Z", {"exit_code": 137},
                f"e2e-137-{uuid.uuid4().hex[:6]}", severity="warning",
            ),
        ],
    )
    _run_pipeline(test_database_url)

    incidents = httpx.get(f"{base}/api/v1/incidents", headers=AUTH, timeout=10).json()
    r1_high = False
    for item in incidents["items"]:
        diags = httpx.get(
            f"{base}/api/v1/incidents/{item['id']}/diagnoses", headers=AUTH, timeout=10
        ).json()
        for d in diags:
            for c in d["candidates"]:
                if c["rule_id"] == "R1" and c["support_level"] == "high":
                    r1_high = True
    assert not r1_high, "仅退出码 137 不得出现 R1 高支持"

    mobile_page.goto(f"{base}/pages/diagnosis")
    mobile_page.wait_for_selector("#incident-table", timeout=15000)
    assert "事件单" in mobile_page.inner_text("main")
