"""R1 容器 OOM evaluator 单元测试。

- OOMKilled / cgroup oom_kill 增量正例 → 高支持
- 仅退出码 137 反例 → 不成立并给出缺失证据
- VM 内存不足 → 并列候选
- 缺客户端观测 → 支持降级
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.control_plane.integrations.protocols import DiagnosticContext

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def make_context(*, events=None, evidence=None, tasks=None) -> DiagnosticContext:
    return DiagnosticContext(
        symptom_resource_id="11111111-1111-1111-1111-111111111111",
        incident_id="inc-1",
        symptom_time=T0,
        window=(T0 - timedelta(seconds=120), T0 + timedelta(seconds=30)),
        events=events or [],
        evidence=evidence or [],
        tasks=tasks or [],
        topology_nodes=[
            {"id": "11111111-1111-1111-1111-111111111111", "kind": "container"},
            {"id": "22222222-2222-2222-2222-222222222222", "kind": "vm"},
            {"id": "33333333-3333-3333-3333-333333333333", "kind": "service_instance"},
        ],
        topology_edges=[
            {"from_id": "22222222-2222-2222-2222-222222222222", "type": "contains", "to_id": "11111111-1111-1111-1111-111111111111"},
            {"from_id": "11111111-1111-1111-1111-111111111111", "type": "runs", "to_id": "33333333-3333-3333-3333-333333333333"},
        ],
    )


def ev_oomkilled() -> dict:
    return {
        "id": "e-oom-1",
        "type": "container.oom",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "occurred_at": T0.isoformat(),
        "severity": "error",
        "payload": {"oom_killed": True, "exit_code": 137},
    }


def ev_cgroup_increment() -> dict:
    return {
        "id": "e-cg-1",
        "type": "container.cgroup_oom",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "occurred_at": T0.isoformat(),
        "severity": "error",
        "payload": {"oom_kill_delta": 3},
    }


def ev_exit137_only() -> dict:
    return {
        "id": "e-exit-1",
        "type": "container.exit",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "occurred_at": T0.isoformat(),
        "severity": "warning",
        "payload": {"exit_code": 137},
    }


def ev_vm_memory(available_ratio: float) -> dict:
    return {
        "id": "e-vm-1",
        "type": "vm.memory.sample",
        "resource_id": "22222222-2222-2222-2222-222222222222",
        "occurred_at": (T0 - timedelta(seconds=5)).isoformat(),
        "severity": "info",
        "payload": {"available_ratio": available_ratio},
    }


def task_failed(client_observed: bool = True) -> dict:
    return {
        "task_id": "task-1",
        "status": "failed",
        "status_provenance": "observed" if client_observed else "inferred",
        "service_instance_id": "33333333-3333-3333-3333-333333333333",
        "error_class": "client_timeout" if client_observed else None,
    }


def build_evaluator():
    from apps.control_plane.diagnosis.evaluators.r1_oom import build

    return build()


def test_oomkilled_positive_high() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(
        make_context(events=[ev_oomkilled()], tasks=[task_failed()])
    )
    assert result.applicable
    assert result.support_level == "high"
    assert result.supporting
    assert "R1" == result.rule_id


def test_cgroup_increment_positive() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(make_context(events=[ev_cgroup_increment()], tasks=[task_failed()]))
    assert result.applicable
    assert result.support_level == "high"


def test_exit_137_only_not_oom() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(make_context(events=[ev_exit137_only()], tasks=[task_failed()]))
    # 仅退出码 137：不得判定 OOM
    assert not result.applicable
    missing = " ".join(result.missing)
    assert "OOMKilled" in missing or "oom_kill" in missing or "内核" in missing


def test_no_oom_evidence_lists_missing() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(make_context(events=[ev_exit137_only()], tasks=[task_failed()]))
    assert any("137" in m or "OOM" in m for m in result.missing)


def test_vm_memory_insufficient_parallel_candidate() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(
        make_context(
            events=[ev_oomkilled(), ev_vm_memory(available_ratio=0.03)],
            tasks=[task_failed()],
        )
    )
    assert result.applicable
    # VM 内存不足时必须出现并列候选（容器 OOM 与 VM 内存压力并存）
    others = getattr(result, "additional_candidates", [])
    assert others, "VM 内存不足应产生并列候选"
    assert any("VM" in c.title or "vm" in c.title.lower() for c in others)


def test_vm_memory_normal_is_contradicting() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(
        make_context(events=[ev_oomkilled(), ev_vm_memory(available_ratio=0.62)], tasks=[task_failed()])
    )
    assert result.applicable
    # VM 内存正常的反证只反证 VM 压力候选，不影响容器 OOM 结论
    others = getattr(result, "additional_candidates", [])
    assert not any("VM" in c.title for c in others)
    assert not others


def test_missing_client_observation_degrades_support() -> None:
    evaluator = build_evaluator()
    strong = evaluator.evaluate(make_context(events=[ev_oomkilled()], tasks=[task_failed(True)]))
    weak = evaluator.evaluate(
        make_context(
            events=[ev_oomkilled()],
            tasks=[
                task_failed(False) | {"status": "interrupted", "error_class": None},
            ],
        )
    )
    assert strong.support_level == "high"
    assert weak.support_level in ("medium", "low")
    assert any("客户端" in m for m in weak.missing)


def test_no_task_failure_degrades() -> None:
    evaluator = build_evaluator()
    result = evaluator.evaluate(make_context(events=[ev_oomkilled()], tasks=[]))
    assert result.applicable
    assert result.support_level in ("medium", "low")
    assert any("任务" in m or "task" in m.lower() for m in result.missing)
