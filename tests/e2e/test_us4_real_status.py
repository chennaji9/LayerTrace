"""US4 E2E：真实任务、指标更新、平台能力、断采 stale/unknown 和补传。

说明：真实 Docker/ZSvirt 环境在 CI 外部；本测试验证可自动化的部分——
任务写入、workloads/tasks/status API 的诚实状态与页面呈现。
"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


@pytest.fixture()
def e2e_env(test_database_url, clean_db, live_server):
    return live_server


def _api(base: str, method: str, path: str, **kwargs) -> httpx.Response:
    res = httpx.request(method, f"{base}{path}", headers=AUTH, timeout=60, **kwargs)
    assert res.status_code == 200, f"{path} -> {res.status_code}: {res.text[:200]}"
    return res


def _post_events(base: str, events: list[dict]) -> None:
    res = httpx.post(
        f"{base}/api/v1/events:batch",
        json={"schema_version": "1.0", "events": events},
        headers=AUTH,
        timeout=30,
    )
    assert res.status_code == 202, res.text


def test_us4_status_tasks_workloads(e2e_env, test_database_url) -> None:
    base = e2e_env
    # 种子：服务实例别名（事件身份解析需要）
    from tests.e2e.conftest import run_async

    def _seed() -> str:
        from datetime import UTC, datetime

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from tests.seed_utils import seed_resource

        async def _do() -> str:
            engine = create_async_engine(test_database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                si = await seed_resource(
                    session, kind="service_instance", provider="otel", source_id="e2e-us4-si",
                    namespace="otel.service_instance", external_id="e2e-us4-si",
                    display_name="demo-agent 实例",
                    now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC),
                )
                await session.commit()
                await engine.dispose()
                return str(si.id)

        return run_async(_do())

    _seed()

    # 真实任务事件（经正式 API）
    tag = "e2e-us4"
    _post_events(base, [
        {
            "schema_version": "1.0",
            "event_id": f"{tag}-start",
            "event_type": "task.started",
            "resource_ref": {"namespace": "otel.service_instance", "external_id": f"{tag}-si"},
            "occurred_at": "2026-09-21T18:00:00Z",
            "observed_at": "2026-09-21T18:00:00Z",
            "source": "demo-agent",
            "provenance": "observed",
            "correlation": {"task_id": f"{tag}-t1"},
            "attributes": {"phase": "model"},
        },
        {
            "schema_version": "1.0",
            "event_id": f"{tag}-done",
            "event_type": "task.succeeded",
            "resource_ref": {"namespace": "otel.service_instance", "external_id": f"{tag}-si"},
            "occurred_at": "2026-09-21T18:00:05Z",
            "observed_at": "2026-09-21T18:00:05Z",
            "source": "demo-agent",
            "provenance": "observed",
            "correlation": {"task_id": f"{tag}-t1"},
            "attributes": {"duration_ms": 5000, "model_duration_ms": 3000, "tool_duration_ms": 1500},
        },
    ])

    # 运行管线（独立线程执行异步投影）

    def _pipeline() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.control_plane.tasks.projection import apply_task_events

        async def _do() -> None:
            engine = create_async_engine(test_database_url)
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                await apply_task_events(session)
                await session.commit()
            await engine.dispose()

        run_async(_do())

    _pipeline()

    # 任务 API：分段耗时
    tasks = _api(base, "GET", "/api/v1/tasks?status=succeeded").json()
    target = next(t for t in tasks["items"] if t["task_id"] == f"{tag}-t1")
    assert target["model_duration_ms"] == 3000
    assert target["tool_duration_ms"] == 1500

    # 状态 API：诚实状态（PG healthy、ZSvirt not_configured）
    status = _api(base, "GET", "/api/v1/status").json()
    states = {s["type"]: s["state"] for s in status["sources"]}
    assert states.get("postgres") in ("healthy", "degraded")
    assert states.get("zsvirt") == "not_configured"

    # 工作负载 API：容器为空时诚实返回空列表（不伪造）
    workloads = _api(base, "GET", "/api/v1/workloads").json()
    assert isinstance(workloads["items"], list)


def test_us4_pages_render(e2e_env, desktop_page) -> None:
    base = e2e_env
    desktop_page.goto(f"{base}/pages/tasks")
    desktop_page.wait_for_selector("#task-table", timeout=20000)
    assert "Agent 任务" in desktop_page.inner_text("main")

    desktop_page.goto(f"{base}/pages/status")
    desktop_page.wait_for_selector("#status-table", timeout=20000)
    assert "数据源状态" in desktop_page.inner_text("main")

    desktop_page.goto(f"{base}/pages/workloads")
    desktop_page.wait_for_selector("#workload-table", timeout=20000)
    assert "工作负载" in desktop_page.inner_text("main")
