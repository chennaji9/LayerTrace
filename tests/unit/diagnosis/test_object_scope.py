"""Counterexamples: simultaneous sibling failures are not causal evidence."""

from datetime import UTC, datetime, timedelta

from apps.control_plane.diagnosis.association import (
    affected_instances,
    affected_tasks,
    baseline_instance_starts,
)
from apps.control_plane.diagnosis.evaluators.r1_oom import build as r1
from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import evaluate_r2
from apps.control_plane.diagnosis.evaluators.r3_tool_failure import evaluate_r3
from apps.control_plane.diagnosis.evaluators.r4_resource_pressure import evaluate_r4
from apps.control_plane.integrations.protocols import DiagnosticContext


def context():
    now = datetime.now(UTC)
    return DiagnosticContext(
        symptom_resource_id="a",
        incident_id="incident",
        symptom_time=now,
        window=(now - timedelta(seconds=120), now + timedelta(seconds=30)),
        topology_nodes=[
            {"id": "a", "kind": "container"},
            {"id": "b", "kind": "container"},
            {"id": "a-instance", "kind": "service_instance"},
            {"id": "b-instance", "kind": "service_instance"},
        ],
        topology_edges=[
            {"from_id": "a", "type": "runs", "to_id": "a-instance"},
            {"from_id": "b", "type": "runs", "to_id": "b-instance"},
        ],
    )


def test_r1_does_not_borrow_sibling_oom_or_task():
    ctx = context()
    ctx.events = [
        {
            "id": "oom-b",
            "type": "container.oom",
            "resource_id": "b",
            "payload": {"oom_killed": True},
        }
    ]
    ctx.tasks = [
        {
            "task_id": "task-b",
            "service_instance_id": "b-instance",
            "status": "failed",
            "status_provenance": "observed",
        }
    ]
    assert not r1().evaluate(ctx).applicable
    ctx.events.append(
        {
            "id": "oom-a",
            "type": "container.oom",
            "resource_id": "a",
            "payload": {"oom_killed": True},
        }
    )
    result = r1().evaluate(ctx)
    assert result.applicable and result.support_level == "low"
    assert "oom-b" not in result.supporting


def test_r2_does_not_borrow_sibling_throttling():
    ctx = context()
    raw = dict(ctx.__dict__)
    raw["baseline"] = {"state": "ready", "p95_ms": 700, "sample_count": 40}
    raw["events"] = [
        {
            "id": "slow",
            "type": "task.succeeded",
            "resource_id": "a-instance",
            "payload": {"duration_ms": 2400},
        },
        {
            "id": "quota-b",
            "type": "container.stats.sample",
            "resource_id": "b",
            "payload": {"cpu_quota": 10000, "throttled_periods_delta": 90},
        },
    ]
    assert not evaluate_r2(raw)["applicable"]


def test_exact_model_dependency_does_not_expand_to_other_service_generations():
    ctx = {
        "symptom_resource_id": "model-container",
        "topology_nodes": [
            {"id": "model-container", "kind": "container"},
            *[
                {"id": key, "kind": "service_instance"}
                for key in ("model-new", "model-old", "agent-new", "agent-old")
            ],
            {"id": "model-service", "kind": "service"},
            {"id": "agent-service", "kind": "service"},
        ],
        "topology_edges": [
            {"from_id": "model-container", "type": "runs", "to_id": "model-new"},
            {"from_id": "model-new", "type": "instance_of", "to_id": "model-service"},
            {"from_id": "model-old", "type": "instance_of", "to_id": "model-service"},
            {"from_id": "agent-new", "type": "instance_of", "to_id": "agent-service"},
            {"from_id": "agent-old", "type": "instance_of", "to_id": "agent-service"},
            {"from_id": "agent-new", "type": "depends_on", "to_id": "model-new"},
            {"from_id": "agent-old", "type": "depends_on", "to_id": "model-old"},
        ],
    }
    assert affected_instances(ctx) == {"model-container", "model-new", "agent-new"}


def test_unchanged_agent_baseline_begins_when_new_model_dependency_begins():
    old = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    replacement = old + timedelta(minutes=20)
    ctx = {
        "symptom_resource_id": "model-container",
        "topology_nodes": [
            {"id": "model-container", "kind": "container", "first_seen": replacement},
            {"id": "model-new", "kind": "service_instance", "first_seen": replacement},
            {"id": "agent", "kind": "service_instance", "first_seen": old},
        ],
        "topology_edges": [
            {
                "from_id": "model-container",
                "type": "runs",
                "to_id": "model-new",
                "valid_from": replacement,
            },
            {
                "from_id": "agent",
                "type": "depends_on",
                "to_id": "model-new",
                "valid_from": replacement,
            },
        ],
    }
    assert baseline_instance_starts(ctx)["agent"] == replacement


def test_new_model_cannot_borrow_pre_dependency_latency_or_crossing_task():
    old = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    replacement = old + timedelta(minutes=20)
    ctx = {
        "symptom_resource_id": "model-container",
        "topology_nodes": [
            {"id": "model-container", "kind": "container", "first_seen": replacement},
            {"id": "model-new", "kind": "service_instance", "first_seen": replacement},
            {"id": "agent", "kind": "service_instance", "first_seen": old},
        ],
        "topology_edges": [
            {
                "from_id": "model-container",
                "type": "runs",
                "to_id": "model-new",
                "valid_from": replacement,
            },
            {
                "from_id": "agent",
                "type": "depends_on",
                "to_id": "model-new",
                "valid_from": replacement,
            },
        ],
        "baseline": {"state": "ready", "p95_ms": 1000, "sample_count": 30},
        "events": [
            {
                "id": "before",
                "type": "task.succeeded",
                "resource_id": "agent",
                "occurred_at": replacement - timedelta(seconds=20),
                "payload": {"duration_ms": 10000},
            },
            {
                "id": "crossing",
                "type": "task.failed",
                "resource_id": "agent",
                "occurred_at": replacement + timedelta(seconds=20),
                "payload": {"duration_ms": 30000},
                "correlation": {"task_id": "crossing-task"},
            },
            {
                "id": "current-normal",
                "type": "task.succeeded",
                "resource_id": "agent",
                "occurred_at": replacement + timedelta(seconds=30),
                "payload": {"duration_ms": 1000},
            },
            {
                "id": "quota",
                "type": "container.stats.sample",
                "resource_id": "model-container",
                "occurred_at": replacement + timedelta(seconds=30),
                "payload": {"cpu_quota": 15000, "throttled_periods_delta": 100},
            },
        ],
        "tasks": [
            {
                "task_id": "crossing-task",
                "service_instance_id": "agent",
                "status": "failed",
                "started_at": replacement - timedelta(seconds=10),
            }
        ],
    }
    assert affected_tasks(ctx) == []
    assert evaluate_r2(ctx)["applicable"] is False
    ctx["events"].append(
        {
            "id": "current-slow",
            "type": "model.completed",
            "resource_id": "model-new",
            "occurred_at": replacement + timedelta(seconds=40),
            "payload": {"duration_ms": 4000},
        }
    )
    result = evaluate_r2(ctx)
    assert result["support_level"] == "high"
    assert {"before", "crossing"}.isdisjoint(result["supporting"])


def test_r3_missing_task_id_cannot_borrow_failed_task():
    ctx = context()
    raw = dict(ctx.__dict__)
    raw["events"] = [
        {"id": "tool", "type": "tool.failed", "resource_id": "a", "payload": {"http_status": 503}}
    ]
    raw["tasks"] = [{"task_id": "other", "service_instance_id": "a-instance", "status": "failed"}]
    result = evaluate_r3(raw)
    assert result["support_level"] == "low"
    assert any("task_id" in item for item in result["missing"])


def test_r3_sibling_healthy_sample_does_not_raise_support():
    ctx = context()
    raw = dict(ctx.__dict__)
    raw["events"] = [
        {
            "id": "tool",
            "type": "tool.failed",
            "resource_id": "a",
            "payload": {"http_status": 503},
            "correlation": {"task_id": "one"},
        },
        {
            "id": "healthy-b",
            "type": "container.stats.sample",
            "resource_id": "b",
            "payload": {"cpu_percent": 0, "throttled_periods_delta": 0},
        },
    ]
    raw["tasks"] = [{"task_id": "one", "service_instance_id": "a-instance", "status": "failed"}]
    result = evaluate_r3(raw)
    assert result["support_level"] == "medium"
    assert not result["contradicting"]


def r4_context():
    ctx = dict(context().__dict__)
    ctx["topology_nodes"].extend([{"id": "vm", "kind": "vm"}, {"id": "host", "kind": "host"}])
    ctx["topology_edges"].extend(
        [
            {"from_id": "vm", "type": "contains", "to_id": "a"},
            {"from_id": "host", "type": "hosts", "to_id": "vm"},
        ]
    )
    ctx["baseline"] = {"state": "ready", "p95_ms": 700, "sample_count": 40}
    ctx["events"] = [
        {
            "id": "slow",
            "type": "task.succeeded",
            "resource_id": "a-instance",
            "payload": {"duration_ms": 2400},
        },
        {
            "id": "steal",
            "type": "vm.cpu.steal.sample",
            "resource_id": "vm",
            "payload": {"steal_percent": 18},
        },
    ]
    return ctx


def test_r4_generic_platform_alarm_cannot_raise_support():
    ctx = r4_context()
    ctx["events"].append(
        {
            "id": "unrelated-platform",
            "type": "platform.alarm",
            "source": "zsvirt",
            "resource_id": "vm",
            "payload": {"message": "VM alarm"},
        }
    )
    result = evaluate_r4(ctx)
    assert result["applicable"] and result["support_level"] == "medium"
    assert "unrelated-platform" not in result["supporting"]


def test_r4_host_cpu_evidence_must_match_owning_host():
    ctx = r4_context()
    ctx["events"].append(
        {
            "id": "host-cpu",
            "type": "host.cpu.sample",
            "source": "zsvirt",
            "resource_id": "other-host",
            "payload": {"cpu_percent": 99},
        }
    )
    assert evaluate_r4(ctx)["support_level"] == "medium"
    ctx["events"][-1]["resource_id"] = "host"
    result = evaluate_r4(ctx)
    assert result["support_level"] == "high" and "host-cpu" in result["supporting"]


def test_r4_does_not_borrow_other_vm_steal():
    ctx = r4_context()
    ctx["events"][1]["resource_id"] = "other-vm"
    assert not evaluate_r4(ctx)["applicable"]
