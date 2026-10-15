"""R2 evaluator：容器 CPU 配额瓶颈。

判定（与 rules/R2-cpu-quota.yaml 绑定）：
- 容器配额存在 + throttling 增量 > 0 + 延迟升高（1 分钟 P95 > 基线 2 倍且
  绝对增加 > 500ms）→ high；
- 基线样本 < 30 → 输出"基线不足"，不得成立；
- 无 throttling 增量 → 不成立（反例）。
延迟基线来自共享服务（T083），展示窗口、样本数和基线值。
"""

from __future__ import annotations

import math
from pathlib import Path

import yaml

from apps.control_plane.diagnosis.association import related_event
from apps.control_plane.diagnosis.baseline import _percentile
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext

RULE_ID = "R2"
EVALUATOR_VERSION = "1.0.2"
RULE_FILE = Path(__file__).resolve().parents[4] / "rules" / "R2-cpu-quota.yaml"

_MODEL_TERMINALS = {"model.completed", "model.succeeded", "model.failed"}
_LATENCY_TERMINALS = {"task.succeeded", "task.failed", "task.timed_out", *_MODEL_TERMINALS}


def _metadata() -> dict:
    return yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))


def _owned_instances(ctx: dict) -> dict[str, dict]:
    """Only the symptom's own runtime instances, excluding dependencies."""
    nodes = {node["id"]: node for node in ctx.get("topology_nodes", [])}
    symptom = ctx.get("symptom_resource_id")
    own = {symptom} if nodes.get(symptom, {}).get("kind") == "service_instance" else set()
    own.update(
        edge.get("to_id")
        for edge in ctx.get("topology_edges", [])
        if edge.get("type") == "runs" and edge.get("from_id") == symptom
    )
    return {key: nodes[key] for key in own if nodes.get(key, {}).get("kind") == "service_instance"}


def model_phase_matches_symptom(event: dict, ctx: dict) -> bool:
    """Bind a model receipt to its actual carrier through exact instance identity."""
    own = _owned_instances(ctx)
    if event.get("type", "").startswith("model."):
        return event.get("resource_id") in {ctx.get("symptom_resource_id"), *own}
    model_instance = event.get("payload", {}).get("model_instance_id")
    return bool(model_instance) and any(
        node.get("source_id") == model_instance for node in own.values()
    )


def _latency_events(events: list[dict], ctx: dict, duration_field: str) -> list[dict]:
    bound = []
    own_resources = {ctx.get("symptom_resource_id"), *_owned_instances(ctx)}
    for event in events:
        if not _latencies([event], duration_field):
            continue
        payload = event.get("payload") or {}
        # related_event reconstructs a terminal-only start from this field.
        # Invalid timing must not fall back to the event time and bypass a
        # generation boundary, even if the selected model value is valid.
        interval_ms = payload.get("duration_ms", payload.get("model_duration_ms"))
        if not _positive_finite_duration(interval_ms):
            continue
        try:
            if not related_event(event, ctx):
                continue
        except (OverflowError, ValueError):
            # A receipt without a representable execution interval cannot
            # establish eligibility for this runtime generation.
            continue
        if duration_field == "model_duration_ms":
            if not model_phase_matches_symptom(event, ctx):
                continue
        else:
            if event.get("resource_id") not in own_resources:
                continue
            if event.get("type", "").startswith("task.") and (
                payload.get("phase") in {"model", "tool"}
                or payload.get("model_instance_id")
                or payload.get("tool_instance_id")
                or any(
                    payload.get(field) not in (None, 0)
                    for field in ("model_duration_ms", "tool_duration_ms")
                )
            ):
                # A task receipt carrying delegated identity or duration is
                # aggregate evidence, including missing/invalid phase durations.
                # Its total latency cannot establish the caller's own CPU delay.
                continue
        bound.append(event)
    return bound


def _latency_field(event: dict, duration_field: str) -> str:
    """Native model terminals report a single closed phase in duration_ms.

    An explicitly supplied model field takes priority, including invalid values.
    Task totals and tool timings must never stand in for a model phase.
    """
    if (
        duration_field == "model_duration_ms"
        and event.get("type") in _MODEL_TERMINALS
        and duration_field not in (event.get("payload") or {})
    ):
        return "duration_ms"
    return duration_field


def _duration_scope(event: dict, source_field: str) -> str:
    if event.get("type") in _MODEL_TERMINALS:
        return "model_phase" if source_field == "duration_ms" else "explicit_model_duration"
    return "task_model_total" if source_field == "model_duration_ms" else "task_total"


def _positive_finite_duration(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _latencies(events: list[dict], duration_field="duration_ms") -> list[int | float]:
    values = []
    for event in events:
        if event.get("type") not in _LATENCY_TERMINALS:
            continue
        value = (event.get("payload") or {}).get(_latency_field(event, duration_field))
        if _positive_finite_duration(value):
            values.append(value)
    return values


def evaluate_r2(ctx: dict) -> dict:
    baseline = ctx.get("baseline") or {}
    events = ctx.get("events", [])
    symptom = ctx.get("symptom_resource_id")
    missing: list[str] = []
    reasons: list[str] = []

    # 延迟升高判据
    if baseline.get("state") != "ready":
        missing.append("基线不足（样本 < 30，无法判断延迟升高）")
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": missing,
            "baseline": baseline,
        }

    p95 = float(baseline.get("p95_ms") or 0)
    duration_field = baseline.get("duration_field", "duration_ms")
    latency_events = _latency_events(events, ctx, duration_field)
    recent = _latencies(latency_events, duration_field)
    if not recent:
        missing.append("窗口内与症状容器自身执行阶段匹配的延迟样本")
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": missing,
            "baseline": baseline,
        }
    current = _percentile(recent, 0.95)
    relative_ok = current > p95 * 2
    absolute_ok = (current - p95) > 500
    if not (relative_ok and absolute_ok):
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": ["延迟未同时满足 2 倍相对与 500ms 绝对升高门槛"],
            "baseline": baseline,
        }

    # 配额 + throttling
    quota_events = [
        e
        for e in events
        if e.get("type") == "container.stats.sample"
        and e.get("resource_id") == symptom
        and (e.get("payload", {}).get("cpu_quota") or 0) > 0
    ]
    throttled = sum(
        int(e.get("payload", {}).get("throttled_periods_delta") or 0) for e in quota_events
    )
    if not quota_events:
        missing.append("容器 CPU 配额数据")
    if throttled <= 0:
        missing.append("CPU throttling 周期增量（配额瓶颈的必要证据）")
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": missing,
            "baseline": baseline,
        }

    latency_samples = [
        {
            "event_id": event["id"],
            "source_field": _latency_field(event, duration_field),
            "duration_scope": _duration_scope(event, _latency_field(event, duration_field)),
            "value_ms": _latencies([event], duration_field)[0],
        }
        for event in latency_events
    ]
    has_model_phase = any(sample["duration_scope"] == "model_phase" for sample in latency_samples)
    baseline_scope = "task_model_total" if duration_field == "model_duration_ms" else "task_total"
    scope_note = "当前延迟按回执中声明的耗时字段独立取样；不跨任务或阶段求和。"
    if has_model_phase:
        baseline_label = "任务累计模型耗时" if baseline_scope == "task_model_total" else "任务总耗时"
        scope_note = (
            f"当前回执包含单模型阶段耗时，按{baseline_label}的保守下界与基线比较；"
            "各回执独立，不跨任务或阶段求和。"
        )

    reasons.append(f"throttling 周期增量 {throttled}")
    reasons.append(f"延迟 {current}ms > 基线 P95 {p95}ms × 2 且绝对增加 > 500ms")
    supporting = [e["id"] for e in quota_events]
    supporting.extend(
        e["id"]
        for e in latency_events
        if e.get("type")
        in {
            "task.succeeded",
            "task.failed",
            "task.timed_out",
            "model.completed",
            "model.succeeded",
            "model.failed",
        }
    )
    return {
        "applicable": True,
        "support_level": "high",
        "supporting": supporting,
        "supporting_reasons": reasons,
        "contradicting_reasons": [],
        "missing": missing,
        "baseline": {
            "window_seconds": baseline.get("window_seconds"),
            "sample_count": baseline.get("sample_count"),
            "p95_ms": p95,
            "threshold": f"> {p95 * 2:.0f}ms（2 倍）且绝对增加 > 500ms",
            "current_p95_ms": current,
            "current_sample_count": len(recent),
            "throttled_periods_delta": throttled,
            "quota_us": sorted({e.get("payload", {}).get("cpu_quota") for e in quota_events}),
            "throttled_ratios": [
                e.get("payload", {}).get("throttled_ratio")
                for e in quota_events
                if "throttled_ratio" in e.get("payload", {})
            ],
            "duration_field": baseline.get("duration_field", "duration_ms"),
            "baseline_duration_scope": baseline_scope,
            "current_latency_samples": latency_samples,
            "duration_scope_note": scope_note,
            "window_end": baseline.get("window_end"),
        },
    }


class R2CpuQuotaEvaluator:
    rule_id = RULE_ID
    version = EVALUATOR_VERSION

    def __init__(self) -> None:
        self._metadata = _metadata()

    def evaluate_ctx(self, ctx: dict) -> dict:
        result = evaluate_r2(ctx)
        result.setdefault("recommendations", list(self._metadata.get("recommendations", [])))
        result.setdefault("verification", list(self._metadata.get("verification_steps", [])))
        return result

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation:
        ctx = _adapt(context)
        result = self.evaluate_ctx(ctx)
        return CandidateEvaluation(
            rule_id=RULE_ID,
            rule_version=EVALUATOR_VERSION,
            title="容器 CPU 配额瓶颈",
            support_level=result["support_level"],
            supporting=result["supporting"],
            contradicting=[],
            missing=result["missing"],
            recommendations=result.get("recommendations", []),
            verification=result.get("verification", []),
            applicable=result["applicable"],
            detail={
                "reasons": result.get("supporting_reasons", []),
                "baseline": result.get("baseline"),
            },
        )


def _adapt(context: DiagnosticContext) -> dict:
    return {
        "symptom_resource_id": context.symptom_resource_id,
        "events": context.events,
        "tasks": context.tasks,
        "topology_nodes": context.topology_nodes,
        "topology_edges": context.topology_edges,
        "baseline": getattr(context, "baseline", None)
        or {"state": "insufficient", "sample_count": 0},
    }


def build() -> R2CpuQuotaEvaluator:
    return R2CpuQuotaEvaluator()
