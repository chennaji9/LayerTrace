"""US5 E2E：确认、静默、事实继续更新、恢复和重开流程。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


@pytest.fixture()
def e2e_env(test_database_url, clean_db, live_server):
    return live_server


def _api(base: str, method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(method, f"{base}{path}", headers=AUTH, timeout=60, **kwargs)


def test_us5_incident_operations(e2e_env) -> None:
    base = e2e_env
    # 运行回放形成事件单
    res = _api(base, "POST", "/api/v1/scenarios/story-oom:run", json={"mode": "replay"})
    assert res.status_code == 202
    incidents = _api(base, "GET", "/api/v1/incidents?state=open").json()
    assert incidents["items"], "应存在打开的事件单"
    incident = incidents["items"][0]
    fid = incident["fingerprint"]

    # 1) 确认 → acknowledged
    res = _api(
        base,
        "POST",
        f"/api/v1/incidents/{incident['id']}:acknowledge",
        json={"actor": "e2e-ops", "note": "处理中"},
    )
    assert res.status_code == 200
    assert res.json()["state"] == "acknowledged"

    # 2) 静默命中：事实继续更新（重复告警仍计数），仅通知被抑制
    res = _api(
        base,
        "POST",
        "/api/v1/silences",
        json={
            "matcher": {"fingerprint": fid},
            "reason": "维护窗口（e2e）",
            "created_by": "e2e-ops",
            "starts_at": "2026-09-21T00:00:00Z",
            "ends_at": "2099-01-01T00:00:00Z",
        },
    )
    assert res.status_code == 201, res.text
    after = _api(base, "GET", f"/api/v1/incidents/{incident['id']}").json()
    assert after["state"] == "acknowledged"
    assert after["alert_count"] >= 1
    assert after["silenced_until"] == "2099-01-01T00:00:00Z"

    # 3) 降噪计数可见
    page = _api(base, "GET", "/api/v1/incidents").json()
    assert "raw_alert_count" in page and "notification_count" in page
    assert page["items"][0]["silenced_until"] == "2099-01-01T00:00:00Z"

    # 4) 重复确认 → 409（状态门禁）
    res = _api(
        base, "POST", f"/api/v1/incidents/{incident['id']}:acknowledge", json={"actor": "e2e-ops"}
    )
    assert res.status_code == 409


def test_us5_recovery_and_reopen(e2e_env, test_database_url) -> None:
    base = e2e_env
    res = _api(base, "POST", "/api/v1/scenarios/story-oom:run", json={"mode": "replay"})
    assert res.status_code == 202
    # 回放同步完成（非活动状态）；再次运行是新的 run（并发冲突只作用于活动 run）
    res2 = _api(base, "POST", "/api/v1/scenarios/story-oom:run", json={"mode": "replay"})
    assert res2.status_code == 202

    incidents = _api(base, "GET", "/api/v1/incidents?state=open").json()
    assert incidents["items"]

    # 恢复流程（线程内异步：全部告警 resolved + 稳定窗口）
    from tests.e2e.conftest import run_async

    def _recover() -> None:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select, update
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.control_plane.incidents.recovery import evaluate_recovery
        from apps.control_plane.models import Alert, Incident

        async def _do() -> None:
            engine = create_async_engine(test_database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                rows = (await session.execute(select(Incident))).scalars().all()
                for incident in rows:
                    await session.execute(
                        update(Alert)
                        .where(Alert.fingerprint == incident.fingerprint)
                        .values(
                            state="resolved", ends_at=datetime.now(UTC) - timedelta(seconds=120)
                        )
                    )
                    await evaluate_recovery(session, incident, now=datetime.now(UTC))
                await session.commit()
            await engine.dispose()

        run_async(_do())

    _recover()
    after = _api(base, "GET", "/api/v1/incidents?state=resolved").json()
    assert after["items"], "全部告警恢复并稳定 60 秒后事件单应恢复"

    # 重开：同一指纹再次触发（新 OOM 事件）
    incidents_all = _api(base, "GET", "/api/v1/incidents").json()
    target = next(i for i in incidents_all["items"] if i["state"] == "resolved")
    occurred_at = datetime.now(UTC).isoformat()
    reopened_event = _api(
        base,
        "POST",
        "/api/v1/events:batch",
        json={
            "schema_version": "1.0",
            "events": [
                {
                    "schema_version": "1.0",
                    "event_id": f"e2e-reopen-{uuid.uuid4().hex[:8]}",
                    "event_type": "container.oom",
                    "resource_ref": {
                        "namespace": "docker.container",
                        "external_id": "story-oom-container-agent",
                    },
                    "occurred_at": occurred_at,
                    "observed_at": occurred_at,
                    "severity": "critical",
                    "source": "e2e",
                    "provenance": "observed",
                    "attributes": {"oom_killed": True},
                }
            ],
        },
    )
    assert reopened_event.status_code == 202, reopened_event.text
    assert reopened_event.json()["unresolved"] == 0

    def _reopen() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.control_plane.incidents.event_alerts import process_event_alerts

        async def _do() -> None:
            engine = create_async_engine(test_database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await process_event_alerts(session)
                await session.commit()
            await engine.dispose()

        run_async(_do())

    _reopen()
    final = _api(base, "GET", f"/api/v1/incidents/{target['id']}").json()
    assert final["state"] == "open", "同一指纹再次触发应重开事件单"


def test_us5_incidents_page(e2e_env, desktop_page) -> None:
    base = e2e_env
    _api(base, "POST", "/api/v1/scenarios/story-oom:run", json={"mode": "replay"})
    desktop_page.goto(f"{base}/pages/incidents")
    desktop_page.wait_for_selector("#noise-panel table", timeout=20000)
    assert "降噪效果" in desktop_page.inner_text("main")
    assert "静默" in desktop_page.inner_text("main")
