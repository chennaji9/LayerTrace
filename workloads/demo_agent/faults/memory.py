"""内存压力故障注入（T119）：限时 OOM、恢复、新实例不串旧任务。

通过受限 Docker 代理调整内存限制并施加分配压力；操作目标必须带
layertrace-test=true 标签（授权层强制）。
"""

from __future__ import annotations

import time


def build_memory_stress(container_name: str, *, limit_bytes: int, seconds: int) -> dict:
    return {
        "container": container_name,
        "memory_limit_bytes": limit_bytes,
        "stress_seconds": seconds,
        "label": "layertrace-test=true",
        "started_at": time.time(),
    }


def should_restore(state: dict, *, now: float | None = None) -> bool:
    now = now or time.time()
    return now - state.get("started_at", now) >= state.get("stress_seconds", 60)


def restart_isolation_check(old_task_ids: list[str], new_instance_tasks: list[str]) -> bool:
    """恢复后新容器实例不得关联到旧任务。"""
    return not set(old_task_ids) & set(new_instance_tasks)
