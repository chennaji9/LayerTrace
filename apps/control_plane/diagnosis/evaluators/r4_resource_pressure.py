"""R4 evaluator：虚拟机/宿主机资源压力（R4a 邻居争用 / R4b guest steal）。

- R4a：症状容器自身无 throttling + 同 VM 邻居容器高占用 + VM CPU 饱和 + 延迟升高；
- R4b：guest steal 高 + ZSvirt 平台证据 → 平台证据缺失时最高 medium；
- VM 未饱和 → 反例不成立；
- 延迟判据使用共享基线服务（T083）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from apps.control_plane.diagnosis.association import owning_containers, related_event
from apps.control_plane.diagnosis.baseline import _percentile
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext

RULE_ID = "R4"
EVALUATOR_VERSION = "1.0.0"
RULE_FILE = Path(__file__).resolve().parents[4] / "rules" / "R4-resource-pressure.yaml"


def _metadata() -> dict:
    return yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))


def _latencies(events: list[dict], duration_field="duration_ms") -> list[int]:
    return [
        e["payload"][duration_field]
        for e in events
        if e.get("type") in {"task.succeeded", "task.failed", "task.timed_out", "model.completed"}
        and isinstance(e.get("payload", {}).get(duration_field), int | float)
    ]


def _scope(ctx: dict) -> tuple[set[str], set[str], set[str]]:
    nodes = {node["id"]: node for node in ctx.get("topology_nodes", [])}
    edges = ctx.get("topology_edges", [])
    symptom = ctx.get("symptom_resource_id")
    kind = nodes.get(symptom, {}).get("kind")
    own = owning_containers(ctx, {symptom})
    vms = {symptom} if kind == "vm" else set()
    if kind == "host":
        vms.update(
            edge["to_id"]
            for edge in edges
            if edge.get("from_id") == symptom and edge.get("type") == "hosts"
        )
    vms.update(
        edge["from_id"]
        for edge in edges
        if edge.get("type") == "contains" and edge.get("to_id") in own
    )
    containers = {
        edge["to_id"]
        for edge in edges
        if edge.get("from_id") in vms and edge.get("type") == "contains"
    }
    return vms, containers, own


def _host_cpu_evidence(ctx: dict, vm_ids: set[str], threshold: float) -> list[dict]:
    hosts = {
        edge["from_id"]
        for edge in ctx.get("topology_edges", [])
        if edge.get("type") == "hosts" and edge.get("to_id") in vm_ids
    }
    out = []
    for event in ctx.get("events", []):
        if event.get("type") not in {
            "host.cpu.sample",
            "platform.host_cpu.sample",
            "zsvirt.host_cpu.sample",
            "zsvirt.host.cpu.sample",
        }:
            continue
        if not event.get("source", "").startswith("zsvirt"):
            continue
        if event.get("resource_id") not in hosts:
            continue
        cpu = event.get("payload", {}).get("cpu_percent")
        if isinstance(cpu, int | float) and cpu >= threshold:
            out.append(event)
    return out


def evaluate_r4(ctx: dict) -> dict:
    thresholds = _metadata().get("thresholds", {})
    vm_saturated = float(thresholds.get("vm_cpu_saturated_percent", 90.0))
    neighbor_high = float(thresholds.get("neighbor_cpu_high_percent", 80.0))
    steal_high = float(thresholds.get("steal_high_percent", 10.0))

    events = ctx.get("events", [])
    baseline = ctx.get("baseline") or {}
    vm_ids, container_ids, own_containers = _scope(ctx)

    missing: list[str] = []
    if baseline.get("state") != "ready":
        missing.append("基线不足（样本 < 30）")
    p95 = float(baseline.get("p95_ms") or 0)
    latency_events = [event for event in events if related_event(event, ctx)]
    recent = _latencies(latency_events, baseline.get("duration_field", "duration_ms"))
    current_p95 = _percentile(recent, 0.95)
    latency_elevated = bool(
        baseline.get("state") == "ready"
        and p95
        and current_p95 > p95 * 2
        and (current_p95 - p95) > 500
    )
    if not latency_elevated:
        missing.append("延迟未同时满足 2 倍相对与 500ms 绝对升高门槛")

    # 症状容器自身 throttling（有 → 更像 R2，不是 R4）
    symptom_throttled = sum(
        int(e.get("payload", {}).get("throttled_periods_delta") or 0)
        for e in events
        if e.get("type") == "container.stats.sample" and e.get("resource_id") in own_containers
    )
    if symptom_throttled > 0:
        return {
            "applicable": False,
            "support_level": "low",
            "supporting": [],
            "supporting_reasons": [],
            "contradicting_reasons": [],
            "missing": ["症状容器存在自身 throttling（指向 R2 配额瓶颈而非邻居争用）"],
        }

    # VM 饱和样本
    vm_cpu = [
        e
        for e in events
        if e.get("type") in ("vm.cpu.sample", "vm.cpu.steal.sample")
        and e.get("resource_id") in vm_ids
    ]
    vm_high = [e for e in vm_cpu if (e.get("payload", {}).get("cpu_percent") or 0) >= vm_saturated]
    steal_events = [
        e
        for e in events
        if e.get("type") == "vm.cpu.steal.sample" and e.get("resource_id") in vm_ids
    ]
    steal_high_events = [
        e for e in steal_events if (e.get("payload", {}).get("steal_percent") or 0) >= steal_high
    ]

    # 邻居高占用
    neighbors_high = [
        e
        for e in events
        if e.get("type") == "container.stats.sample"
        and e.get("resource_id") not in own_containers
        and e.get("resource_id") in container_ids
        and (e.get("payload", {}).get("cpu_percent") or 0) >= neighbor_high
    ]

    # Inventory, VM state and generic alarms do not establish host CPU pressure.
    platform_events = _host_cpu_evidence(ctx, vm_ids, vm_saturated)
    has_platform = bool(platform_events)

    supporting: list[str] = []
    reasons: list[str] = []
    support = "low"
    applicable = False

    if latency_elevated and vm_high and neighbors_high:
        applicable = True
        support = "high"
        supporting = [e["id"] for e in vm_high + neighbors_high]
        reasons.append("症状容器无自身 throttling")
        reasons.append(f"同 VM 邻居容器 CPU ≥ {neighbor_high}%")
        reasons.append(f"VM CPU 饱和 ≥ {vm_saturated}%")
        return {
            "applicable": applicable,
            "support_level": support,
            "supporting": supporting,
            "supporting_reasons": reasons,
            "contradicting_reasons": [],
            "missing": missing,
            "variant": "R4a",
        }

    if latency_elevated and steal_high_events:
        applicable = True
        support = "medium" if not has_platform else "high"
        supporting = [e["id"] for e in steal_high_events + platform_events]
        reasons.append(f"guest steal ≥ {steal_high}%")
        if not has_platform:
            missing.append("ZSvirt 平台侧宿主机 CPU 指标（缺少直接证据，最高 medium）")
        return {
            "applicable": applicable,
            "support_level": support,
            "supporting": supporting,
            "supporting_reasons": reasons,
            "contradicting_reasons": [],
            "missing": missing,
            "variant": "R4b",
        }

    if not vm_high and not steal_high_events:
        missing.append("VM 未饱和且无高 steal（资源压力反例）")
    return {
        "applicable": False,
        "support_level": "low",
        "supporting": [],
        "supporting_reasons": [],
        "contradicting_reasons": [],
        "missing": missing,
    }


class R4ResourcePressureEvaluator:
    rule_id = RULE_ID
    version = EVALUATOR_VERSION

    def __init__(self) -> None:
        self._metadata = _metadata()

    def evaluate_ctx(self, ctx: dict) -> dict:
        result = evaluate_r4(ctx)
        result.setdefault("recommendations", list(self._metadata.get("recommendations", [])))
        result.setdefault("verification", list(self._metadata.get("verification_steps", [])))
        return result

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation:
        result = self.evaluate_ctx(
            {
                "symptom_resource_id": context.symptom_resource_id,
                "events": context.events,
                "tasks": context.tasks,
                "topology_nodes": context.topology_nodes,
                "topology_edges": context.topology_edges,
                "baseline": getattr(context, "baseline", None)
                or {"state": "insufficient", "sample_count": 0},
            }
        )
        title = "虚拟机/宿主机资源压力"
        if result.get("variant") == "R4a":
            title = "同 VM 邻居资源争用（R4a）"
        elif result.get("variant") == "R4b":
            title = "宿主机资源压力 / guest steal（R4b）"
        return CandidateEvaluation(
            rule_id=RULE_ID,
            rule_version=EVALUATOR_VERSION,
            title=title,
            support_level=result["support_level"],
            supporting=result["supporting"],
            contradicting=[],
            missing=result["missing"],
            recommendations=result.get("recommendations", []),
            verification=result.get("verification", []),
            applicable=result["applicable"],
            detail={
                "reasons": result.get("supporting_reasons", []),
                "variant": result.get("variant"),
            },
        )


def build() -> R4ResourcePressureEvaluator:
    return R4ResourcePressureEvaluator()
