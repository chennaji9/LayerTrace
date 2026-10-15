"""CPU 负载注入（T122）：带测试标签、最长 3 分钟的同 VM CPU 负载及恢复。"""

from __future__ import annotations

import time

MAX_DURATION_SECONDS = 180
LABEL = "layertrace-test=true"


def build_load(*, cores: int, duration_seconds: int) -> dict:
    duration = min(duration_seconds, MAX_DURATION_SECONDS)
    return {
        "cores": max(1, cores),
        "duration_seconds": duration,
        "label": LABEL,
        "started_at": time.time(),
    }


def burn(state: dict) -> None:
    """按状态消耗 CPU（多进程忙循环；到时自动退出）。"""
    import multiprocessing

    workers = []
    for _ in range(state["cores"]):
        proc = multiprocessing.Process(target=_spin, args=(state["duration_seconds"],), daemon=True)
        proc.start()
        workers.append(proc)
    for proc in workers:
        proc.join()


def _spin(seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        pass


def load_label_check(state: dict) -> bool:
    return state.get("label") == LABEL and state["duration_seconds"] <= MAX_DURATION_SECONDS
