"""US2 E2E：8 页面回放来源徽标、由正式 API 渲染的完整当前拓扑、OOM 诊断、
剧本答案不出现在诊断响应。"""

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
    return httpx.request(method, f"{base}{path}", headers=AUTH, timeout=60, **kwargs)


def _run_via_api(base: str, name: str) -> dict:
    res = _api(base, "POST", f"/api/v1/scenarios/{name}:run", json={"mode": "replay"})
    assert res.status_code == 202, res.text
    return res.json()


def test_us2_replay_full_chain(desktop_page, origin_assert, e2e_env) -> None:
    base = e2e_env
    run = _run_via_api(base, "story-oom")
    assert run["state"] == "succeeded"

    # 1) 当前拓扑由正式 API 渲染（页面数据仅来自 /api/v1/*）
    desktop_page.goto(f"{base}/pages/topology")
    desktop_page.wait_for_selector("#topo-meta", timeout=20000)
    meta = desktop_page.inner_text("#topo-meta")
    assert "节点" in meta and "关系" in meta

    topology = _api(base, "GET", "/api/v1/topology").json()
    assert topology["nodes"], "回放后拓扑必须可见"
    kinds = {n["kind"] for n in topology["nodes"]}
    assert {"host", "vm", "container", "service_instance"} <= kinds
    assert all(n["provenance"] == "replayed" for n in topology["nodes"])
    assert all(e["provenance"] == "replayed" for e in topology["edges"])

    # 2) 事件单与 OOM 诊断
    desktop_page.goto(f"{base}/pages/diagnosis")
    desktop_page.wait_for_selector("#incident-table tbody tr button", timeout=20000)
    desktop_page.click("#incident-table tbody tr:first-child button")
    desktop_page.wait_for_selector(".candidate", timeout=20000)
    body = desktop_page.inner_text("#d-candidates")
    assert "容器内存不足" in body and "high" in body

    # 3) 剧本答案不出现在产品诊断响应
    incidents = _api(base, "GET", "/api/v1/incidents").json()
    assert incidents["incident_count"] >= 1
    for item in incidents["items"]:
        detail = _api(base, "GET", f"/api/v1/incidents/{item['id']}").json()
        diagnoses = _api(base, "GET", f"/api/v1/incidents/{item['id']}/diagnoses").json()
        for payload in (detail, *diagnoses):
            blob = str(payload).lower()
            assert "expected_assertion" not in blob
            assert "verification_result" not in blob

    # verify 独立使用期望断言
    res = _api(base, "POST", f"/api/v1/scenario-runs/{run['id']}:verify")
    assert res.status_code == 200
    assert res.json()["verification_result"]["passed"]

    assert any("/api/v1/" in p for p in origin_assert.data_requests)


def test_us2_replay_badges_on_pages(desktop_page, e2e_env) -> None:
    base = e2e_env
    _run_via_api(base, "story-oom")
    desktop_page.goto(f"{base}/pages/topology")
    desktop_page.wait_for_selector(".prov-replayed", timeout=20000)
    badge = desktop_page.inner_text(".prov-replayed")
    assert badge.strip() == "回放"
