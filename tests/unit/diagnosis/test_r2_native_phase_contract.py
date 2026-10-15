"""Native closed model phases remain usable without an in-window task receipt.

The saved CPU12 fixture is a read-only replay of a failed real run, not a
formal acceptance result. Other cases guard attribution and duration scope.
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import evaluate_r2
from tests.unit.diagnosis.test_r2_phase_attribution import (
    FIRST,
    IDS,
    MODEL_GENERATION,
    NOW,
    fixture,
)

pytestmark = pytest.mark.unit


def native_phase_context(event_type="model.completed", duration_ms=4000):
    ctx = fixture()
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"] = [e for e in ctx["events"] if e["type"] == "container.stats.sample"]
    ctx["events"].append(
        {
            "id": "native-model-phase",
            "type": event_type,
            "resource_id": IDS["target-instance"],
            "occurred_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "correlation": {"task_id": "native-task"},
            "payload": {"phase": "initial", "duration_ms": duration_ms},
        }
    )
    return ctx


def test_cpu12_native_phase_contract_from_saved_readonly_context():
    path = Path(__file__).resolve().parents[2] / "fixtures/diagnosis/cpu12-r2-native-phase-context.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    ctx = saved["context"]
    original = copy.deepcopy(ctx)
    before = saved["observed_output_before_repair"]
    assert before["applicable"] is False
    assert before["missing"] == ["窗口内与症状容器自身执行阶段匹配的延迟样本"]
    result = evaluate_r2(ctx)
    assert result["applicable"] and result["support_level"] == "high"
    assert result["baseline"]["p95_ms"] == 51247
    assert result["baseline"]["sample_count"] == 35
    assert result["baseline"]["current_p95_ms"] == 165108
    assert result["baseline"]["current_sample_count"] == 1
    assert "13a60b36-f891-4d3b-8075-34ea08b691e7" in result["supporting"]
    assert "e79c5e63-d686-433d-a7b3-e0e856da075c" not in result["supporting"]
    assert result["baseline"]["baseline_duration_scope"] == "task_model_total"
    assert result["baseline"]["current_latency_samples"] == [
        {
            "event_id": "13a60b36-f891-4d3b-8075-34ea08b691e7",
            "source_field": "duration_ms",
            "duration_scope": "model_phase",
            "value_ms": 165108,
        }
    ]
    assert "保守下界" in result["baseline"]["duration_scope_note"]
    assert ctx == original


@pytest.mark.parametrize("event_type", ["model.completed", "model.succeeded", "model.failed"])
def test_native_closed_model_phase_is_a_conservative_sample(event_type):
    ctx = native_phase_context(event_type)
    original = copy.deepcopy(ctx)
    result = evaluate_r2(ctx)
    assert result["applicable"] and result["support_level"] == "high"
    assert result["baseline"]["duration_field"] == "model_duration_ms"
    assert result["baseline"]["current_p95_ms"] == 4000
    sample = result["baseline"]["current_latency_samples"][0]
    assert sample["source_field"] == "duration_ms"
    assert sample["duration_scope"] == "model_phase"
    assert ctx == original


@pytest.mark.parametrize("value", [None, "4000", 0, -1, True, False, float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("explicit_model_field", [False, True])
def test_invalid_or_nonfinite_duration_cannot_supply_latency(value, explicit_model_field):
    ctx = native_phase_context(duration_ms=4000 if explicit_model_field else value)
    if explicit_model_field:
        ctx["events"][-1]["payload"]["model_duration_ms"] = value
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert result["supporting"] == []
    assert "自身执行阶段" in " ".join(result["missing"])


@pytest.mark.parametrize("explicit_model_ms,native_ms,expected", [(900, 8000, False), (4000, 1, True)])
def test_explicit_model_duration_takes_priority(explicit_model_ms, native_ms, expected):
    ctx = native_phase_context(duration_ms=native_ms)
    ctx["events"][-1]["payload"]["model_duration_ms"] = explicit_model_ms
    result = evaluate_r2(ctx)
    assert result["applicable"] is expected
    if expected:
        sample = result["baseline"]["current_latency_samples"][0]
        assert sample["source_field"] == "model_duration_ms"
        assert sample["duration_scope"] == "explicit_model_duration"
        assert result["baseline"]["current_p95_ms"] == explicit_model_ms


def test_unclosed_model_start_is_not_a_duration_sample():
    ctx = native_phase_context("model.started")
    ctx["events"][-1]["payload"]["model_duration_ms"] = 8000
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]
    assert "自身执行阶段" in " ".join(result["missing"])


def test_native_slow_phase_still_requires_throttling():
    ctx = native_phase_context()
    for event in ctx["events"]:
        if event["type"] == "container.stats.sample":
            event["payload"]["throttled_periods_delta"] = 0
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "CPU throttling" in " ".join(result["missing"])


@pytest.mark.parametrize("resource", ["caller-instance", "caller-container", "retired-model", "sibling-model"])
def test_phase_on_other_carrier_cannot_borrow_target_quota(resource):
    ctx = native_phase_context()
    ctx["events"][-1]["resource_id"] = IDS.get(resource, resource)
    ctx["events"][-1]["payload"]["model_instance_id"] = MODEL_GENERATION
    if resource in {"retired-model", "sibling-model"}:
        ctx["topology_nodes"].append(
            {"id": resource, "kind": "service_instance", "source_id": resource + "-generation", "first_seen": FIRST.isoformat()}
        )
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]


@pytest.mark.parametrize("bound_kind", ["generation", "runs-edge"])
def test_native_phase_cannot_cross_generation_or_runtime_binding(bound_kind):
    ctx = native_phase_context()
    recent = (NOW - timedelta(seconds=1)).isoformat()
    if bound_kind == "generation":
        node = next(n for n in ctx["topology_nodes"] if n["id"] == IDS["target-instance"])
        node["first_seen"] = recent
    else:
        edge = next(e for e in ctx["topology_edges"] if e["type"] == "runs" and e["to_id"] == IDS["target-instance"])
        edge["valid_from"] = recent
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]


@pytest.mark.parametrize("event_type", ["task.succeeded", "task.failed", "task.timed_out", "tool.completed", "tool.failed"])
def test_other_terminal_types_cannot_fall_back_to_total_duration(event_type):
    ctx = native_phase_context(event_type)
    event = ctx["events"][-1]
    event["resource_id"] = IDS["caller-instance"]
    event["payload"]["model_instance_id"] = MODEL_GENERATION
    event["payload"]["tool_duration_ms"] = 8000
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]


def test_task_total_cannot_replace_an_explicit_normal_model_duration():
    ctx = native_phase_context("task.succeeded", 8000)
    event = ctx["events"][-1]
    event["resource_id"] = IDS["caller-instance"]
    event["payload"].update(model_instance_id=MODEL_GENERATION, model_duration_ms=900)
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "升高门槛" in " ".join(result["missing"])


def test_tool_slow_task_cannot_establish_callers_own_cpu_delay():
    ctx = fixture("caller-container", "duration_ms")
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"][0]["payload"].update(duration_ms=8000, model_duration_ms=900, tool_duration_ms=6500)
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "自身执行阶段" in " ".join(result["missing"])


@pytest.mark.parametrize("different_task", [False, True])
def test_initial_and_post_tool_synthesis_are_not_added_together(different_task):
    ctx = native_phase_context(duration_ms=1800)
    synthesis = copy.deepcopy(ctx["events"][-1])
    synthesis["id"] = "synthesis-phase"
    synthesis["payload"]["phase"] = "synthesis"
    if different_task:
        synthesis["correlation"]["task_id"] = "another-task"
    ctx["events"].append(synthesis)
    ctx["events"].append(
        {"id": "slow-tool", "type": "tool.completed", "resource_id": IDS["target-instance"], "occurred_at": NOW.isoformat(), "payload": {"duration_ms": 6500}}
    )
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "升高门槛" in " ".join(result["missing"])


@pytest.mark.parametrize("p95,latency,expected", [(1000, 2000, False), (1000, 2001, True), (100, 600, False), (100, 601, True)])
def test_native_phase_retains_both_strict_latency_thresholds(p95, latency, expected):
    ctx = native_phase_context(duration_ms=latency)
    ctx["baseline"]["p95_ms"] = p95
    result = evaluate_r2(ctx)
    assert result["applicable"] is expected


def test_callers_quota_cannot_borrow_a_native_dependency_phase():
    ctx = native_phase_context()
    ctx["symptom_resource_id"] = IDS["caller-container"]
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]


def test_r2_rule_metadata_matches_phase_contract_evaluator():
    from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import RULE_FILE
    from apps.control_plane.diagnosis.rule_loader import load_rule

    rule = load_rule(RULE_FILE)
    assert rule.version == rule.evaluator.version == "1.0.2"


@pytest.mark.parametrize(
    "native_timing", [None, "4000", 0, -1, True, False, float("nan"), float("inf"), -float("inf"), 1e308]
)
def test_invalid_execution_interval_cannot_borrow_valid_explicit_model_value(native_timing):
    ctx = native_phase_context(duration_ms=native_timing)
    ctx["events"][-1]["payload"]["model_duration_ms"] = 4000
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]


def test_unrepresentable_finite_phase_interval_cannot_prove_generation_eligibility():
    result = evaluate_r2(native_phase_context(duration_ms=1e308))
    assert result["applicable"] is False
    assert "native-model-phase" not in result["supporting"]
