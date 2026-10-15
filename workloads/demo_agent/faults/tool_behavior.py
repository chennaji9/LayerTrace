"""工具行为故障注入（T121）：deadline/503、恢复与"不归因基础设施"断言。

故障状态写入 FAULT_STATE 文件（tool.py 读取），到期自动失效。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

FAULT_STATE = Path("/tmp/layertrace-tool-fault.json")


def inject(mode: str, *, seconds: int, tool: str | None = None) -> dict:
    state = {
        "mode": mode,  # deadline | http_503
        "tool": tool,
        "started_at": time.time(),
        "expires_at": time.time() + seconds,
        "label": "layertrace-test=true",
    }
    FAULT_STATE.write_text(json.dumps(state), encoding="utf-8")
    return state


def clear() -> None:
    FAULT_STATE.unlink(missing_ok=True)


def active() -> dict | None:
    try:
        state = json.loads(FAULT_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if state.get("expires_at") and time.time() > state["expires_at"]:
        clear()
        return None
    return state


def infra_blame_assertion(container_stats: dict) -> bool:
    """不归因基础设施断言：注入期间容器资源保持正常。"""
    return (container_stats.get("cpu_percent") or 0) < 60 and not (
        container_stats.get("throttled_periods_delta") or 0
    )
