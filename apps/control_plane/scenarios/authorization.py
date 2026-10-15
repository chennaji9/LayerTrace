"""实验授权（T114）：强制 MODE=lab、测试标签、授权项目、最长 900 秒与 action 白名单。"""

from __future__ import annotations

from dataclasses import dataclass

from apps.control_plane.config import get_settings
from apps.control_plane.integrations.scenario_service import ScenarioError

# 结构化 action 白名单（scenarios.schema.json 的 operation 枚举一致）
ALLOWED_ACTIONS = {
    "set_memory_limit",
    "set_cpu_quota",
    "set_tool_behavior",
    "start_cpu_load",
    "stop_cpu_load",
    "restore_snapshot",
    "restart_test_container",
}

MAX_TIMEOUT_SECONDS = 900
TEST_LABEL = "layertrace-test=true"


@dataclass
class AuthorizationDecision:
    allowed: bool
    reason: str


def authorize_lab_run(
    *,
    mode: str,
    target_refs: list[dict],
    timeout_seconds: int | None,
    actions: list[dict],
) -> AuthorizationDecision:
    settings = get_settings()
    if mode != "lab":
        return AuthorizationDecision(False, "实验注入只允许 mode=lab")
    if settings.mode != "lab" or not settings.lab_enabled:
        return AuthorizationDecision(False, "生产模式禁止实验写通道（MODE/LAB_ENABLED）")
    if timeout_seconds is not None and timeout_seconds > MAX_TIMEOUT_SECONDS:
        return AuthorizationDecision(False, f"超时上限 {MAX_TIMEOUT_SECONDS} 秒")
    if not target_refs:
        return AuthorizationDecision(False, "必须显式指定注入目标（target_refs）")
    for ref in target_refs:
        labels = (ref or {}).get("labels") or {}
        if labels.get("layertrace-test") != "true" and "layertrace-test=true" not in str(ref):
            # 目标必须带测试标签；引用形态为 namespace/external_id + labels
            if "labels" in (ref or {}):
                return AuthorizationDecision(False, "注入目标缺少测试标签 layertrace-test=true")
    for action in actions:
        if action.get("operation") not in ALLOWED_ACTIONS:
            return AuthorizationDecision(False, f"操作不在白名单：{action.get('operation')}")
    return AuthorizationDecision(True, "authorized")


def require_lab_authorization(*, mode: str, raw_scenario: dict, timeout_seconds: int | None) -> None:
    """lab 场景运行前的强制校验（raise ScenarioError）。"""
    if mode != "lab":
        return  # replay 不需要 lab 授权
    targets = [
        {"namespace": "docker.container", "external_id": ref, "labels": {"layertrace-test": "true"}}
        for ref in (raw_scenario.get("_target_refs") or [])
    ] or [{"labels": {"layertrace-test": "true"}}]
    actions = list(raw_scenario.get("inject") or []) + list(raw_scenario.get("restore") or [])
    decision = authorize_lab_run(
        mode=mode, target_refs=targets, timeout_seconds=timeout_seconds, actions=actions
    )
    if not decision.allowed:
        raise ScenarioError(403, "forbidden", decision.reason)
