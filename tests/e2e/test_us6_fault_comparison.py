"""US6 E2E：回放侧故障区分（B/R2 与 D1 类比对照需要真实 lab，此处验证
规则区分能力与全部恢复路径中可自动化的部分）。"""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


@pytest.fixture()
def e2e_env(test_database_url, clean_db, live_server):
    return live_server


def test_us6_scenario_files_replay_and_restore(e2e_env) -> None:
    base = e2e_env
    # 回放类剧本：运行 → 验证 → 恢复（恢复路径对回放是记录性 no-op）
    for name in ("story-oom", "story-host-pressure"):
        res = httpx.post(f"{base}/api/v1/scenarios/{name}:run",
                         json={"mode": "replay"}, headers=AUTH, timeout=60)
        assert res.status_code == 202, res.text
        run = res.json()
        assert run["state"] == "succeeded"

        res = httpx.post(f"{base}/api/v1/scenario-runs/{run['id']}:verify",
                         headers=AUTH, timeout=60)
        assert res.status_code == 200
        assert res.json()["verification_result"]["passed"], (name, res.json())

        res = httpx.post(f"{base}/api/v1/scenario-runs/{run['id']}:restore",
                         headers=AUTH, timeout=60)
        assert res.status_code == 202
        assert res.json()["state"] == "restored"
        assert res.json()["restore_result"]["success"] is True


def test_us6_lab_mode_rejected_in_prod(e2e_env) -> None:
    base = e2e_env
    res = httpx.post(f"{base}/api/v1/scenarios/a1-container-oom:run",
                     json={"mode": "lab"}, headers=AUTH, timeout=60)
    assert res.status_code == 403, "生产模式必须拒绝 lab 注入"
    assert "lab" in res.text.lower() or "实验" in res.text


def test_us6_lab_page_disabled_state(e2e_env, desktop_page) -> None:
    base = e2e_env
    desktop_page.goto(f"{base}/pages/lab")
    desktop_page.wait_for_selector("#lab-disabled", timeout=20000)
    text = desktop_page.inner_text("#lab-disabled")
    assert "实验通道未启用" in text
    assert "layertrace-test=true" in text  # 明示注入范围与风险
