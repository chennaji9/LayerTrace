"""US6 单元测试：R2 CPU 配额瓶颈、R3 工具失败、R4 资源压力。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def make_context(*, events=None, tasks=None, baseline=None, topology=None) -> dict:
    """构造 evaluator 用的轻量上下文 dict（服务层适配 DiagnosticContext）。"""
    return {
        "symptom_resource_id": "11111111-1111-1111-1111-111111111111",
        "incident_id": "inc-1",
        "symptom_time": T0,
        "window": (T0 - timedelta(seconds=120), T0 + timedelta(seconds=30)),
        "events": events or [],
        "tasks": tasks or [],
        "snapshots": [],
        "alerts": [],
        "evidence": [],
        "topology_nodes": topology
        or [
            {"id": "11111111-1111-1111-1111-111111111111", "kind": "container"},
            {"id": "22222222-2222-2222-2222-222222222222", "kind": "vm"},
        ],
        "topology_edges": [
            {
                "from_id": "22222222-2222-2222-2222-222222222222",
                "type": "contains",
                "to_id": node["id"],
            }
            for node in (
                topology or [{"id": "11111111-1111-1111-1111-111111111111", "kind": "container"}]
            )
            if node["kind"] == "container"
        ],
        "time_confidence": "trusted",
        "data_gaps": [],
        "context_truncated": False,
        "baseline": baseline
        or {
            "state": "insufficient",
            "sample_count": 0,
            "p95_ms": None,
            "window_seconds": 900,
            "multiplier": 3,
            "agent_timeout_ms": None,
        },
    }


def vm_event(cpu_percent: float, at=None) -> dict:
    return {
        "id": f"e-vm-{cpu_percent}",
        "type": "vm.cpu.sample",
        "resource_id": "22222222-2222-2222-2222-222222222222",
        "occurred_at": (at or T0 - timedelta(seconds=10)).isoformat(),
        "severity": "info",
        "payload": {"cpu_percent": cpu_percent, "steal_percent": 0.5},
    }


def container_quota_event(quota_cores: float, throttled_delta: int, at=None) -> dict:
    return {
        "id": f"e-cq-{throttled_delta}",
        "type": "container.stats.sample",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "occurred_at": (at or T0 - timedelta(seconds=5)).isoformat(),
        "severity": "info",
        "payload": {
            "cpu_percent": 95.0,
            "throttled_periods_delta": throttled_delta,
            "cpu_quota": int(quota_cores * 100000),
            "cpu_period_us": 100000,
        },
    }


def task_latency_event(latency_ms: int, at=None) -> dict:
    return {
        "id": f"e-tl-{latency_ms}",
        "type": "task.succeeded",
        "resource_id": "11111111-1111-1111-1111-111111111111",
        "occurred_at": (at or T0 - timedelta(seconds=2)).isoformat(),
        "severity": "info",
        "payload": {"duration_ms": latency_ms},
    }


# --- R2 CPU 配额瓶颈 ---


def test_r2_quota_throttling_latency_positive() -> None:
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import build

    evaluator = build()
    ctx = make_context(
        events=[container_quota_event(0.5, throttled_delta=200), task_latency_event(2400)],
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 800,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2400,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    assert result["applicable"]
    assert result["support_level"] == "high"
    assert any("throttl" in s for s in result["supporting_reasons"])


def test_r2_no_throttling_negative() -> None:
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import build

    evaluator = build()
    ctx = make_context(
        events=[container_quota_event(2.0, throttled_delta=0), task_latency_event(2400)],
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 800,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2400,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    assert not result["applicable"] or "throttling" in " ".join(result["missing"])


def test_r2_insufficient_baseline() -> None:
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import build

    evaluator = build()
    ctx = make_context(
        events=[container_quota_event(0.5, throttled_delta=200), task_latency_event(2400)],
        baseline={
            "state": "insufficient",
            "sample_count": 10,
            "p95_ms": None,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": None,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    assert "基线不足" in " ".join(result["missing"])


def test_r2_latency_not_elevated() -> None:
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import build

    evaluator = build()
    ctx = make_context(
        events=[container_quota_event(0.5, throttled_delta=200), task_latency_event(900)],
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 800,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2400,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    # 延迟未超过基线两倍且绝对增加不足 500ms → 不成立
    assert not result["applicable"]


# --- R3 工具失败 ---


def tool_failed_events(mode: str) -> list[dict]:
    payload = (
        {"tool_name": "lookup_runbook", "http_status": 503}
        if mode == "503"
        else {"tool_name": "lookup_runbook", "deadline_exceeded": True}
    )
    return [
        {
            "id": "e-tf-1",
            "type": "task.started",
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "occurred_at": (T0 - timedelta(seconds=30)).isoformat(),
            "severity": "info",
            "payload": {},
            "correlation": {"task_id": "task-r3"},
        },
        {
            "id": "e-tf-2",
            "type": "tool.failed" if mode == "503" else "tool.timed_out",
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "occurred_at": (T0 - timedelta(seconds=10)).isoformat(),
            "severity": "error",
            "payload": payload,
            "correlation": {"task_id": "task-r3"},
        },
        {
            "id": "e-tf-3",
            "type": "task.failed",
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "occurred_at": (T0 - timedelta(seconds=5)).isoformat(),
            "severity": "error",
            "payload": {"error_class": "tool_failure"},
            "correlation": {"task_id": "task-r3"},
        },
    ]


def test_r3_deadline_positive() -> None:
    from apps.control_plane.diagnosis.evaluators.r3_tool_failure import build

    evaluator = build()
    ctx = make_context(
        events=tool_failed_events("deadline"),
        tasks=[
            {
                "task_id": "task-r3",
                "status": "failed",
                "status_provenance": "observed",
                "error_class": "tool_failure",
            }
        ],
    )
    result = evaluator.evaluate_ctx(ctx)
    assert result["applicable"]
    assert result["support_level"] in ("high", "medium")


def test_r3_http503_positive() -> None:
    from apps.control_plane.diagnosis.evaluators.r3_tool_failure import build

    evaluator = build()
    ctx = make_context(
        events=tool_failed_events("503"),
        tasks=[
            {
                "task_id": "task-r3",
                "status": "failed",
                "status_provenance": "observed",
                "error_class": "tool_failure",
            }
        ],
    )
    result = evaluator.evaluate_ctx(ctx)
    assert result["applicable"]


def test_r3_infrastructure_normal_is_contradicting() -> None:
    from apps.control_plane.diagnosis.evaluators.r3_tool_failure import build

    evaluator = build()
    events = tool_failed_events("503")
    # 基础设施正常反证：同窗口容器资源正常
    events.append(
        {
            "id": "e-infra-ok",
            "type": "container.stats.sample",
            "resource_id": "11111111-1111-1111-1111-111111111111",
            "occurred_at": (T0 - timedelta(seconds=1)).isoformat(),
            "severity": "info",
            "payload": {"cpu_percent": 20.0, "memory_working_set_bytes": 100000000},
        }
    )
    ctx = make_context(events=events)
    result = evaluator.evaluate_ctx(ctx)
    assert result["applicable"]
    assert result["contradicting_reasons"] or "反证" in " ".join(result.get("detail_notes", []))


# --- R4 资源压力 ---


def test_r4a_neighbor_contention() -> None:
    from apps.control_plane.diagnosis.evaluators.r4_resource_pressure import build

    evaluator = build()
    topology = [
        {"id": "11111111-1111-1111-1111-111111111111", "kind": "container"},
        {"id": "33333333-3333-3333-3333-333333333333", "kind": "container"},
        {"id": "22222222-2222-2222-2222-222222222222", "kind": "vm"},
    ]
    events = [
        # 症状容器：自身无 throttling
        container_quota_event(4.0, throttled_delta=0),
        task_latency_event(3000),
        # 同 VM 邻居高占用
        {
            "id": "e-neighbor",
            "type": "container.stats.sample",
            "resource_id": "33333333-3333-3333-3333-333333333333",
            "occurred_at": (T0 - timedelta(seconds=4)).isoformat(),
            "severity": "info",
            "payload": {"cpu_percent": 96.5},
        },
        # VM CPU 饱和
        vm_event(cpu_percent=97.0),
    ]
    ctx = make_context(
        events=events,
        topology=topology,
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 900,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2700,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    assert result["applicable"]
    assert any("邻居" in r or "neighbor" in r.lower() for r in result["supporting_reasons"])


def test_r4b_missing_platform_evidence_caps_medium() -> None:
    from apps.control_plane.diagnosis.evaluators.r4_resource_pressure import build

    evaluator = build()
    # 只有 guest steal 高，无 ZSvirt 平台证据 → 最高 medium
    events = [task_latency_event(3000), vm_event(cpu_percent=95.0)]
    events.append(
        {
            "id": "e-steal",
            "type": "vm.cpu.steal.sample",
            "resource_id": "22222222-2222-2222-2222-222222222222",
            "occurred_at": (T0 - timedelta(seconds=3)).isoformat(),
            "severity": "warning",
            "payload": {"steal_percent": 18.0},
        }
    )
    ctx = make_context(
        events=events,
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 900,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2700,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    if result["applicable"]:
        assert result["support_level"] in ("medium", "low"), "缺平台证据时最高 medium"
        assert any("平台" in m for m in result["missing"])


def test_r4_vm_not_saturated_negative() -> None:
    from apps.control_plane.diagnosis.evaluators.r4_resource_pressure import build

    evaluator = build()
    events = [
        container_quota_event(4.0, throttled_delta=0),
        task_latency_event(3000),
        vm_event(cpu_percent=30.0),
    ]
    ctx = make_context(
        events=events,
        baseline={
            "state": "ready",
            "sample_count": 100,
            "p95_ms": 900,
            "window_seconds": 900,
            "multiplier": 2,
            "agent_timeout_ms": 2700,
        },
    )
    result = evaluator.evaluate_ctx(ctx)
    assert not result["applicable"] or result["support_level"] == "low"
