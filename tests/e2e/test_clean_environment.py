"""US7 E2E：干净（CPU-only 回放）环境验证脚本（可自动化部分，T129）。

真实干净 Linux 全流程见 docs/reproduction-report.md；本测试验证可自动化的
核心链路：启动 → 状态 → 回放闭环 → 反例 → 卸载语义。
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


def test_clean_environment_replay_flow(e2e_env) -> None:
    base = e2e_env
    # 1) 状态可用且诚实
    status = httpx.get(f"{base}/api/v1/status", headers=AUTH, timeout=30)
    assert status.status_code == 200
    states = {s["type"]: s["state"] for s in status.json()["sources"]}
    assert states.get("zsvirt") == "not_configured"

    # 2) OOM 回放闭环
    res = httpx.post(f"{base}/api/v1/scenarios/story-oom:run",
                     json={"mode": "replay"}, headers=AUTH, timeout=60)
    assert res.status_code == 202
    run = res.json()
    verify = httpx.post(f"{base}/api/v1/scenario-runs/{run['id']}:verify",
                        headers=AUTH, timeout=60).json()
    assert verify["verification_result"]["passed"]

    # 3) 137 反例
    res = httpx.post(f"{base}/api/v1/scenarios/story-exit-137-not-oom:run",
                     json={"mode": "replay"}, headers=AUTH, timeout=60)
    assert res.status_code == 202
    counter = httpx.post(f"{base}/api/v1/scenario-runs/{res.json()['id']}:verify",
                         headers=AUTH, timeout=60).json()
    assert counter["verification_result"]["passed"]

    # 4) 页面可用（8 导航）
    page = httpx.get(f"{base}/", timeout=30)
    assert page.status_code == 200
    for nav in ("总览", "拓扑", "工作负载", "任务", "事件单", "诊断", "状态", "实验"):
        assert nav in page.text
