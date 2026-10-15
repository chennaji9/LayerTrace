"""诊断规则加载器。

- 规则元数据是无可执行代码的 YAML，必须通过 diagnostic-rule.schema.json 校验
  （additionalProperties=false 使任意 python/sql 字段在 Schema 层即被拒绝）；
- evaluator 必须是注册表中的已知名称，加载时绑定模块与精确版本；
- 规则版本与 evaluator 实现版本必须一致，避免元数据与行为漂移。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from apps.control_plane.integrations.protocols import DiagnosticRule

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "specs/001-zsvirt-observability-mvp/contracts/diagnostic-rule.schema.json"
)


class RuleConfigError(Exception):
    """规则配置非法（未知 evaluator、版本不匹配、Schema 校验失败、包含可执行内容）。"""


# 已注册 evaluator：名称 -> 模块路径（实现必须在模块内暴露
# EVALUATOR_VERSION 与 build()/类本身；首次实现于 US1/US6）
EVALUATOR_REGISTRY: dict[str, str] = {
    "r0_process_exit": "apps.control_plane.diagnosis.evaluators.r0_process_exit",
    "r1_oom": "apps.control_plane.diagnosis.evaluators.r1_oom",
    "r2_cpu_quota": "apps.control_plane.diagnosis.evaluators.r2_cpu_quota",
    "r3_tool_failure": "apps.control_plane.diagnosis.evaluators.r3_tool_failure",
    "r4_resource_pressure": "apps.control_plane.diagnosis.evaluators.r4_resource_pressure",
}

# 规则文件中出现即整体拒绝的可执行内容指示
FORBIDDEN_RULE_KEYS = {"python", "sql", "exec", "eval", "command", "shell", "script", "code"}


@dataclass
class LoadedRule:
    metadata: dict[str, Any]
    evaluator: DiagnosticRule

    @property
    def rule_id(self) -> str:
        return str(self.metadata["rule_id"])

    @property
    def version(self) -> str:
        return str(self.metadata["version"])


def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _load_evaluator(name: str) -> Any:
    import importlib

    if name not in EVALUATOR_REGISTRY:
        raise RuleConfigError(f"未注册的 evaluator：{name}")
    module = importlib.import_module(EVALUATOR_REGISTRY[name])
    build = getattr(module, "build", None)
    if callable(build):
        return build()
    if hasattr(module, "Evaluator"):
        return module.Evaluator()
    return module


def load_rule(path: Path) -> LoadedRule:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleConfigError(f"规则 YAML 解析失败：{path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuleConfigError(f"规则必须是对象：{path.name}")

    forbidden = FORBIDDEN_RULE_KEYS & set(raw)
    if forbidden:
        raise RuleConfigError(f"规则包含禁止的可执行字段：{sorted(forbidden)}（{path.name}）")

    errors = sorted(_validator().iter_errors(raw), key=lambda e: e.path)
    if errors:
        detail = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        raise RuleConfigError(f"规则未通过 Schema 校验（{path.name}）：{detail}")

    evaluator_name = str(raw["evaluator"])
    if evaluator_name not in EVALUATOR_REGISTRY:
        raise RuleConfigError(
            f"未注册的 evaluator：{evaluator_name}（{path.name}，已注册："
            f"{sorted(EVALUATOR_REGISTRY)}）"
        )

    evaluator = _load_evaluator(evaluator_name)
    declared = str(raw["version"])
    actual = getattr(evaluator, "version", None)
    if actual is not None and actual != declared:
        raise RuleConfigError(
            f"规则版本 {declared} 与 evaluator 实现版本 {actual} 不一致（{path.name}）"
        )

    return LoadedRule(metadata=raw, evaluator=evaluator)


def load_rules(rules_dir: Path) -> list[LoadedRule]:
    rules: list[LoadedRule] = []
    for path in sorted(rules_dir.glob("*.y*ml")):
        rule = load_rule(path)
        if rule.metadata.get("enabled", True):
            rules.append(rule)
    return rules
