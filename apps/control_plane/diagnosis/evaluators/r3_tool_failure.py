"""R3 evaluator：Agent 工具调用失败。

- 同 task 的工具 deadline/超时或 HTTP 5xx 事件 + 任务失败 → 成立；
- 基础设施正常（容器资源正常、无 OOM/throttling）作为"不归因基础设施"的反证；
- 工具失败与容器/VM 资源问题区分：资源证据缺失时明确列出。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from apps.control_plane.diagnosis.association import (
    affected_instances,
    affected_tasks,
    owning_containers,
    related_event,
)
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext

RULE_ID = "R3"
EVALUATOR_VERSION = "1.0.0"
RULE_FILE = Path(__file__).resolve().parents[4] / "rules" / "R3-tool-failure.yaml"

_TOOL_FAILURE_TYPES = ("tool.failed", "tool.timed_out", "tool.error")


def _metadata() -> dict:
    return yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))


def evaluate_r3(ctx: dict) -> dict:
    events = ctx.get("events", [])

    tool_failures = [
        e
        for e in events
        if e.get("type") in _TOOL_FAILURE_TYPES
        and e.get("resource_id") == ctx.get("symptom_resource_id")
    ]
    missing: list[str] = []
    if not tool_failures:
        missing.append("同任务的工具失败事件（deadline/503/错误）")
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": missing,
        }

    # 同 task 关联校验
    failure_task_ids = {(e.get("correlation") or {}).get("task_id") for e in tool_failures} - {None}
    failed_tasks = [
        t for t in affected_tasks(ctx) if t.get("status") in ("failed", "timed_out", "interrupted")
    ]
    correlated = [t for t in failed_tasks if t.get("task_id") in failure_task_ids]
    failed_events = [
        e
        for e in events
        if e.get("type") in {"task.failed", "task.timed_out"}
        and related_event(e, ctx)
        and (e.get("correlation") or {}).get("task_id") in failure_task_ids
    ]
    correlated.extend(failed_events)
    if not correlated:
        missing.append("与工具失败同 task_id 的任务失败记录")

    reasons = []
    for event in tool_failures:
        payload = event.get("payload", {})
        if payload.get("deadline_exceeded"):
            reasons.append(f"工具 {payload.get('tool_name')} deadline 超时")
        elif payload.get("http_status"):
            reasons.append(f"工具 {payload.get('tool_name')} HTTP {payload['http_status']}")
        else:
            reasons.append(f"工具 {payload.get('tool_name', '?')} 失败")

    # 基础设施正常反证（不归因基础设施）
    contradicting = []
    infra_normal = [
        e
        for e in events
        if e.get("type") == "container.stats.sample"
        and e.get("resource_id") in owning_containers(ctx, affected_instances(ctx))
        and e.get("payload", {}).get("cpu_percent") is not None
        and e["payload"]["cpu_percent"] < 60
        and not (e.get("payload", {}).get("throttled_periods_delta") or 0)
    ]
    for event in infra_normal:
        contradicting.append(event["id"])
    if infra_normal:
        reasons.append("容器资源正常（不归因基础设施）")

    support = "high" if correlated and contradicting else "medium" if correlated else "low"
    supporting = [e["id"] for e in tool_failures]
    return {
        "applicable": True,
        "support_level": support,
        "supporting": supporting,
        "supporting_reasons": reasons,
        "contradicting_reasons": ["容器资源正常"] if contradicting else [],
        "contradicting": contradicting,
        "missing": missing,
        "detail_notes": ["工具层失败与容器/VM 资源问题相互独立（反证已列出）"],
    }


class R3ToolFailureEvaluator:
    rule_id = RULE_ID
    version = EVALUATOR_VERSION

    def __init__(self) -> None:
        self._metadata = _metadata()

    def evaluate_ctx(self, ctx: dict) -> dict:
        result = evaluate_r3(ctx)
        result.setdefault("recommendations", list(self._metadata.get("recommendations", [])))
        result.setdefault("verification", list(self._metadata.get("verification_steps", [])))
        return result

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation:
        result = self.evaluate_ctx(
            {
                "events": context.events,
                "tasks": context.tasks,
                "symptom_resource_id": context.symptom_resource_id,
                "topology_nodes": context.topology_nodes,
                "topology_edges": context.topology_edges,
            }
        )
        return CandidateEvaluation(
            rule_id=RULE_ID,
            rule_version=EVALUATOR_VERSION,
            title="Agent 工具调用失败",
            support_level=result["support_level"],
            supporting=result["supporting"],
            contradicting=result.get("contradicting", []),
            missing=result["missing"],
            recommendations=result.get("recommendations", []),
            verification=result.get("verification", []),
            applicable=result["applicable"],
            detail={
                "reasons": result.get("supporting_reasons", []),
                "same_task_required": True,
                "task_ids": sorted(
                    {
                        (e.get("correlation") or {}).get("task_id")
                        for e in context.events
                        if e.get("type") in _TOOL_FAILURE_TYPES
                        and (e.get("correlation") or {}).get("task_id")
                    }
                ),
            },
        )


def build() -> R3ToolFailureEvaluator:
    return R3ToolFailureEvaluator()
