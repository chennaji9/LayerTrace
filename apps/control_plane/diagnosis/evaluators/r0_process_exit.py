"""R0 records a process exit; exit code 137 alone never proves OOM."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import yaml

from apps.control_plane.diagnosis.association import affected_tasks, related_event
from apps.control_plane.diagnosis.evaluators.r1_oom import _has_direct_oom
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext

RULE_ID = "R0"
EVALUATOR_VERSION = "1.0.0"
RULE_FILE = Path(__file__).resolve().parents[4] / "rules" / "R0-process-exit.yaml"
OOM_EXIT_WINDOW_SECONDS = 10


def is_abnormal_exit(event: dict) -> bool:
    if event.get("type") != "container.exit":
        return False
    code = event.get("payload", {}).get("exit_code")
    try:
        return code is not None and int(code) != 0
    except (TypeError, ValueError):
        return False


def has_nearby_direct_oom(events: list[dict], exit_event: dict) -> bool:
    occurred_at = exit_event.get("occurred_at")
    timestamp = datetime.fromisoformat(occurred_at) if occurred_at else None
    nearby = []
    for event in events:
        other_at = event.get("occurred_at")
        if timestamp is not None and other_at:
            if (
                abs((datetime.fromisoformat(other_at) - timestamp).total_seconds())
                > OOM_EXIT_WINDOW_SECONDS
            ):
                continue
        nearby.append(event)
    return _has_direct_oom(nearby, exit_event.get("resource_id"))[0]


class R0ProcessExitEvaluator:
    rule_id = RULE_ID
    version = EVALUATOR_VERSION

    def __init__(self) -> None:
        self._metadata = yaml.safe_load(RULE_FILE.read_text(encoding="utf-8"))

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation:
        exits = [
            event
            for event in context.events
            if event.get("resource_id") == context.symptom_resource_id
            and is_abnormal_exit(event)
            and not has_nearby_direct_oom(context.events, event)
        ]
        result = CandidateEvaluation(
            rule_id=RULE_ID,
            rule_version=EVALUATOR_VERSION,
            title="容器进程异常退出",
            support_level="low",
            applicable=bool(exits),
            supporting=[event["id"] for event in exits],
        )
        if not exits:
            result.missing = ["同容器非零退出码（可信 OOM 已由 R1 处理）"]
            return result

        tasks = [
            task
            for task in affected_tasks(context)
            if task.get("status") in {"failed", "interrupted", "timed_out"}
        ]
        client_failures = [
            event
            for event in context.events
            if event.get("type") in {"task.failed", "task.timed_out"}
            and related_event(event, context)
            and (event.get("correlation") or {}).get("task_id")
        ]
        observed = any(task.get("status_provenance") == "observed" for task in tasks)
        if observed or client_failures:
            result.support_level = "high"
            result.supporting.extend(event["id"] for event in client_failures)
        elif tasks:
            result.support_level = "medium"
            result.missing.append("客户端失败观测（当前仅有实例退出推断）")
        else:
            result.missing.append("退出窗口内关联任务的失败或中断记录")

        result.missing.append("进程退出的具体原因；退出码或 SIGKILL 本身不能证明 OOM")
        result.recommendations = list(self._metadata.get("recommendations", []))
        result.verification = list(self._metadata.get("verification_steps", []))
        result.detail = {
            "container_id": context.symptom_resource_id,
            "exit_observations": [
                {
                    "event_id": event["id"],
                    "exit_code": event.get("payload", {}).get("exit_code"),
                    "signal": event.get("payload", {}).get("signal"),
                    "occurred_at": event.get("occurred_at"),
                }
                for event in exits
            ],
            "affected_task_ids": [task["task_id"] for task in tasks],
            "memory_cause_confirmed": False,
        }
        return result


def build() -> R0ProcessExitEvaluator:
    return R0ProcessExitEvaluator()
