"""R1 evaluator：容器内存不足（OOM）。

判定逻辑（版本 1.0.0，与 rules/R1-container-oom.yaml 绑定）：
- 直接 OOM 证据 = OOMKilled 事件 / cgroup oom_kill 增量 / 可信内核 OOM 事件；
- 仅退出码 137 不构成 OOM（缺失证据明确列出）；
- 支持等级：直接证据 + 客户端观测任务失败 = high；
  直接证据 + 仅推断中断 = medium；直接证据 + 无任务关联 = low；
- VM 内存样本不足 10% 可用时追加"VM 内存不足"并列候选；
- VM 内存正常（≥50% 可用）作为反证（反证 VM 压力，不反证容器 OOM 本身）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from apps.control_plane.diagnosis.association import affected_tasks, related_event
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext

RULE_ID = "R1"
EVALUATOR_VERSION = "1.0.0"
RULE_FILE = Path(__file__).resolve().parents[4] / "rules" / "R1-container-oom.yaml"

_OOM_DIRECT_TYPES = {"container.oom", "container.cgroup_oom", "kernel.oom_kill"}
_VM_MEMORY_TYPES = {"vm.memory.sample", "vm.memory"}
_TASK_FAILURE_STATUS = {"failed", "interrupted", "timed_out"}


def _load_metadata() -> dict:
    return yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))


def _node_kind(context: DiagnosticContext, resource_id: str | None) -> str | None:
    for node in context.topology_nodes:
        if node.get("id") == resource_id:
            return str(node.get("kind"))
    return None


def _vm_nodes(context: DiagnosticContext) -> list[str]:
    return [
        edge["from_id"]
        for edge in context.topology_edges
        if edge.get("type") == "contains" and edge.get("to_id") == context.symptom_resource_id
    ]


def _has_direct_oom(events: list[dict], container_id: str | None) -> tuple[bool, list[dict]]:
    direct: list[dict] = []
    for event in events:
        if event.get("type") not in _OOM_DIRECT_TYPES:
            continue
        payload = event.get("payload", {})
        if payload.get("oom_killed") is True or (payload.get("oom_kill_delta") or 0) > 0:
            direct.append(event)
        elif event.get("type") == "kernel.oom_kill" and payload.get("container_matched") is True:
            direct.append(event)
    if container_id:
        direct = [e for e in direct if e.get("resource_id") == container_id]
    else:
        direct = []
    return bool(direct), direct


def _exit_137_events(events: list[dict], container_id: str | None) -> list[dict]:
    out = []
    for event in events:
        if event.get("type") != "container.exit":
            continue
        if event.get("payload", {}).get("exit_code") == 137:
            out.append(event)
    if container_id:
        out = [e for e in out if e.get("resource_id") == container_id]
    return out


def _vm_memory_samples(context: DiagnosticContext, events: list[dict]) -> list[dict]:
    vm_ids = set(_vm_nodes(context))
    samples = []
    for event in events:
        if event.get("type") in _VM_MEMORY_TYPES:
            if event.get("resource_id") in vm_ids:
                samples.append(event)
    return samples


def _task_evidence(context: DiagnosticContext) -> dict:
    client_observed = []
    inferred = []
    for task in affected_tasks(context):
        if task.get("status") in _TASK_FAILURE_STATUS:
            if task.get("status_provenance") == "observed":
                client_observed.append(task)
            else:
                inferred.append(task)
    task_failed_events = [
        e
        for e in context.events
        if e.get("type") in {"task.failed", "task.timed_out"}
        and related_event(e, context)
        and (e.get("correlation") or {}).get("task_id")
    ]
    return {
        "client_observed": client_observed,
        "inferred": inferred,
        "failed_events": task_failed_events,
        "has_failure": bool(client_observed or inferred or task_failed_events),
        "has_client_observation": bool(client_observed or task_failed_events),
    }


class R1OomEvaluator:
    rule_id = RULE_ID
    version = EVALUATOR_VERSION

    def __init__(self) -> None:
        self._metadata = _load_metadata()

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation:
        thresholds = self._metadata.get("thresholds", {})
        vm_low = float(thresholds.get("vm_memory_available_low", 0.1))
        vm_normal = float(thresholds.get("vm_memory_available_normal", 0.5))

        container_id = context.symptom_resource_id
        events = context.events
        has_direct, direct_events = _has_direct_oom(events, container_id)
        exit137 = _exit_137_events(events, container_id)
        tasks = _task_evidence(context)
        vm_samples = _vm_memory_samples(context, events)

        supporting: list[str] = [e["id"] for e in direct_events]
        contradicting: list[str] = []
        missing: list[str] = []

        if not has_direct:
            # 仅退出码 137（或无任何信号）：不成立，明确缺失证据
            result = CandidateEvaluation(
                rule_id=RULE_ID,
                rule_version=EVALUATOR_VERSION,
                title="容器内存不足（OOM）",
                support_level="low",
                applicable=False,
                supporting=[],
                contradicting=[],
                missing=[
                    "OOMKilled 直接证据",
                    "cgroup memory.events 的 oom_kill 增量",
                    "可信内核 OOM 事件",
                ],
                recommendations=[],
                verification=[],
                detail={"exit_137_events": [e["id"] for e in exit137]},
            )
            return result

        # 支持等级
        if tasks["has_client_observation"]:
            support_level = "high"
            supporting.extend(e["id"] for e in tasks["failed_events"])
        elif tasks["inferred"]:
            support_level = "medium"
            missing.append("客户端失败观测（当前仅有实例退出推断）")
        elif tasks["has_failure"]:
            support_level = "medium"
        else:
            support_level = "low"
            missing.append("窗口内受影响的任务失败记录")

        # 容器内存限制快照作为证据
        for snap in context.snapshots:
            if snap.get("resource_id") == container_id and snap.get("limits"):
                supporting.append(snap["id"])

        # VM 内存样本
        vm_pressure_samples = [
            s for s in vm_samples if s.get("payload", {}).get("available_ratio", 1.0) < vm_low
        ]
        vm_normal_samples = [
            s for s in vm_samples if s.get("payload", {}).get("available_ratio", 1.0) >= vm_normal
        ]
        # VM 内存正常是"VM 层压力"的反证（不影响容器 OOM 结论本身）
        contradicting.extend(e["id"] for e in vm_normal_samples)

        additional = []
        if vm_pressure_samples:
            additional.append(
                CandidateEvaluation(
                    rule_id=RULE_ID,
                    rule_version=EVALUATOR_VERSION,
                    title="VM 内存不足（并列候选：宿主层内存压力）",
                    support_level="medium" if len(vm_pressure_samples) >= 2 else "low",
                    supporting=[s["id"] for s in vm_pressure_samples],
                    contradicting=[],
                    missing=["VM 层内存压力的指标序列证据" if len(vm_pressure_samples) < 2 else ""],
                    recommendations=["检查 VM 内存配额与其他容器的内存占用"],
                    verification=["对比 VM available 与容器 limit 的窗口序列"],
                    detail={"kind": "vm_memory_pressure"},
                )
            )

        result = CandidateEvaluation(
            rule_id=RULE_ID,
            rule_version=EVALUATOR_VERSION,
            title="容器内存不足（OOM）",
            support_level=support_level,
            score=None,
            supporting=supporting,
            contradicting=contradicting,
            missing=missing,
            recommendations=list(self._metadata.get("recommendations", [])),
            verification=list(self._metadata.get("verification_steps", [])),
            detail={
                "direct_evidence_events": [e["id"] for e in direct_events],
                "vm_normal_samples": [e["id"] for e in vm_normal_samples],
                "window": [context.window[0].isoformat(), context.window[1].isoformat()],
                "thresholds": thresholds,
                "container_id": container_id,
                "affected_task_ids": [t["task_id"] for t in affected_tasks(context)],
            },
            additional_candidates=additional,
        )
        return result


def build() -> R1OomEvaluator:
    return R1OomEvaluator()
