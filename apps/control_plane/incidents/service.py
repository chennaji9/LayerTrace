"""事件单服务（T100）：指纹、重复计数、派生折叠、严重度与重开。"""

from __future__ import annotations

import hashlib
import uuid

ALLOWED_TRANSITIONS = {
    "open": {"acknowledged", "resolved"},
    "acknowledged": {"resolved"},
    "resolved": {"open"},  # 同一指纹再次触发 → 重开
}
SEVERITY_RANK = {"P1": 3, "P2": 2, "P3": 1}


def fingerprint_for(project_id: uuid.UUID, rule_id: str, resource_key) -> str:
    """项目 + 规则 + 稳定故障对象 → 事件单指纹。"""
    raw = f"{project_id}|{rule_id}|{resource_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def transition(current: str, target: str) -> bool:
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"事件单状态不允许 {current} -> {target}")
    return True
