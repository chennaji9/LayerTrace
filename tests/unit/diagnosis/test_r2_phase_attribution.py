"""A caller's throttle cannot borrow a dependency's slow model phase.

The runtime path and 174242ms/22747ms figures reproduce the CPU5 evidence
structure; all inputs are local fixtures with no database or live services.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.control_plane.diagnosis.context import build_context
from apps.control_plane.diagnosis.evaluators.r2_cpu_quota import evaluate_r2

pytestmark = pytest.mark.unit

FIRST = datetime(2026, 10, 14, 19, 22, tzinfo=UTC)
NOW = datetime(2026, 10, 14, 20, 4, tzinfo=UTC)
IDS = {
    key: str(uuid.uuid5(uuid.NAMESPACE_URL, key))
    for key in ("caller-container", "caller-instance", "target-container", "target-instance")
}
MODEL_GENERATION = "opaque-target-runtime-generation"
CALLER_GENERATION = "opaque-caller-runtime-generation"


def fixture(symptom="target-container", duration_field="model_duration_ms"):
    nodes = [
        {
            "id": IDS[key],
            "kind": "container" if key.endswith("container") else "service_instance",
            "source_id": MODEL_GENERATION
            if key == "target-instance"
            else CALLER_GENERATION
            if key == "caller-instance"
            else "opaque-" + key,
            "first_seen": FIRST.isoformat(),
        }
        for key in IDS
    ]
    edges = [
        {"from_id": IDS["caller-container"], "type": "runs", "to_id": IDS["caller-instance"]},
        {"from_id": IDS["target-container"], "type": "runs", "to_id": IDS["target-instance"]},
        {"from_id": IDS["caller-instance"], "type": "depends_on", "to_id": IDS["target-instance"]},
    ]
    terminal = {
        "id": "model-latency-receipt",
        "type": "task.succeeded",
        "resource_id": IDS["caller-instance"],
        "occurred_at": NOW.isoformat(),
        "observed_at": NOW.isoformat(),
        "provenance": "observed",
        "correlation": {"task_id": "slow-task"},
        "payload": {
            "phase": "model",
            "duration_ms": 175240,
            "model_duration_ms": 174242,
            "model_instance_id": MODEL_GENERATION,
            "tool_duration_ms": 258,
        },
    }

    def quota(key, value, throttle):
        return {
            "id": key + "-throttle",
            "type": "container.stats.sample",
            "resource_id": IDS[key],
            "occurred_at": NOW.isoformat(),
            "observed_at": NOW.isoformat(),
            "provenance": "observed",
            "payload": {
                "cpu_quota": value,
                "cpu_period_us": 100000,
                "throttled_periods_delta": throttle,
            },
        }

    return {
        "symptom_resource_id": IDS[symptom],
        "topology_nodes": nodes,
        "topology_edges": edges,
        "events": [
            terminal,
            quota("caller-container", 50000, 140),
            quota("target-container", 10000, 400),
        ],
        "tasks": [
            {
                "task_id": "slow-task",
                "service_instance_id": IDS["caller-instance"],
                "started_at": (NOW - timedelta(seconds=175)).isoformat(),
            }
        ],
        "baseline": {
            "state": "ready",
            "sample_count": 35,
            "p95_ms": 22747,
            "duration_field": duration_field,
        },
    }


def test_model_target_retains_high_with_exact_model_generation():
    result = evaluate_r2(fixture())
    assert result["applicable"] and result["support_level"] == "high"
    assert result["baseline"]["current_p95_ms"] == 174242
    assert result["baseline"]["quota_us"] == [10000]
    assert result["supporting"] == ["target-container-throttle", "model-latency-receipt"]


@pytest.mark.parametrize("duration_field", ["model_duration_ms", "duration_ms"])
def test_caller_throttling_cannot_borrow_slow_dependency_phase(duration_field):
    result = evaluate_r2(fixture("caller-container", duration_field))
    assert result["applicable"] is False
    assert result["support_level"] == "low"
    assert result["supporting"] == []
    assert "自身执行阶段" in " ".join(result["missing"])


@pytest.mark.parametrize("generation", [None, "retired-target-generation", CALLER_GENERATION])
def test_model_generation_must_be_explicit_and_match_carrier(generation):
    ctx = fixture()
    ctx["events"][0]["payload"]["model_instance_id"] = generation
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert "model-latency-receipt" not in result["supporting"]


def test_model_total_baseline_cannot_borrow_caller_own_execution():
    ctx = fixture("target-container", "duration_ms")
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"][0]["payload"] = {
        "phase": "agent",
        "duration_ms": 4000,
        "model_duration_ms": 0,
        "tool_duration_ms": 0,
    }
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert result["supporting"] == []


@pytest.mark.parametrize("identity_field", ["model_instance_id", "tool_instance_id"])
@pytest.mark.parametrize(
    "duration_fields",
    [
        {},
        {"model_duration_ms": None, "tool_duration_ms": None},
        {"model_duration_ms": "174242", "tool_duration_ms": "182"},
    ],
)
def test_delegated_identity_blocks_ambiguous_total_duration(identity_field, duration_fields):
    ctx = fixture("caller-container", "duration_ms")
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"][0]["payload"] = {
        "phase": "agent",
        "duration_ms": 4000,
        identity_field: MODEL_GENERATION,
        **duration_fields,
    }
    result = evaluate_r2(ctx)
    assert result["applicable"] is False
    assert result["supporting"] == []


def test_direct_model_terminal_is_valid_with_total_baseline():
    ctx = fixture("target-container", "duration_ms")
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"].append(
        {
            "id": "direct-model-phase",
            "type": "model.completed",
            "resource_id": IDS["target-instance"],
            "occurred_at": NOW.isoformat(),
            "payload": {
                "phase": "model",
                "duration_ms": 4000,
                "model_instance_id": MODEL_GENERATION,
            },
        }
    )
    result = evaluate_r2(ctx)
    assert result["applicable"] and result["support_level"] == "high"
    assert result["baseline"]["current_p95_ms"] == 4000
    assert result["supporting"] == ["target-container-throttle", "direct-model-phase"]


@pytest.mark.parametrize("duration_fields", [{}, {"model_duration_ms": 0, "tool_duration_ms": 0}])
def test_caller_own_execution_latency_and_throttle_remain_valid(duration_fields):
    ctx = fixture("caller-container", "duration_ms")
    ctx["baseline"]["p95_ms"] = 1000
    ctx["events"].append(
        {
            "id": "direct-caller-phase",
            "type": "task.succeeded",
            "resource_id": IDS["caller-instance"],
            "occurred_at": NOW.isoformat(),
            "payload": {
                "phase": "agent",
                "duration_ms": 4000,
                **duration_fields,
            },
        }
    )
    result = evaluate_r2(ctx)
    assert result["applicable"] and result["support_level"] == "high"
    assert result["baseline"]["current_p95_ms"] == 4000
    assert result["baseline"]["quota_us"] == [50000]
    assert result["supporting"] == ["caller-container-throttle", "direct-caller-phase"]


@pytest.mark.parametrize(
    "symptom,model_baseline_ready,expected_calls",
    [
        ("target-container", True, ["model_duration_ms"]),
        ("caller-container", True, ["duration_ms"]),
        ("target-container", False, ["model_duration_ms", "duration_ms"]),
    ],
)
async def test_real_context_selects_only_the_bound_phase_baseline(
    monkeypatch, symptom, model_baseline_ready, expected_calls
):
    from apps.control_plane.diagnosis import baseline as service
    from apps.control_plane.diagnosis import context as context_module

    raw = fixture(symptom)
    nodes = [
        SimpleNamespace(
            id=uuid.UUID(n["id"]),
            kind=n["kind"],
            source_id=n["source_id"],
            first_seen=FIRST,
            display_name="arbitrary",
            provenance="observed",
        )
        for n in raw["topology_nodes"]
    ]
    edges = [
        SimpleNamespace(
            id=uuid.uuid4(),
            from_id=uuid.UUID(e["from_id"]),
            type=e["type"],
            to_id=uuid.UUID(e["to_id"]),
            valid_from=FIRST,
            valid_to=None,
            source="test",
            provenance="observed",
        )
        for e in raw["topology_edges"]
    ]
    row = raw["events"][0]
    event = SimpleNamespace(
        id=uuid.uuid4(),
        type=row["type"],
        resource_id=uuid.UUID(row["resource_id"]),
        resource_ref={},
        occurred_at=NOW,
        observed_at=NOW,
        severity="info",
        payload=copy.deepcopy(row["payload"]),
        correlation=row["correlation"],
        source="test",
        provenance="observed",
    )
    alert = SimpleNamespace(
        id=uuid.uuid4(),
        resource_id=uuid.UUID(IDS[symptom]),
        rule_id="R2",
        state="firing",
        severity="warning",
        starts_at=NOW,
        ends_at=None,
    )
    values = iter([[alert], [event], [], []])

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def scalars(self):
            return self

        def all(self):
            return self.rows

    class Session:
        async def execute(self, _query):
            return Result(next(values))

    monkeypatch.setattr(
        context_module, "effective_topology", AsyncMock(return_value=(nodes, edges, False))
    )
    monkeypatch.setattr(service, "alert_exclusion_windows", AsyncMock(return_value=[]))
    calls = []

    async def compute(_session, **kwargs):
        field = kwargs.get("duration_field", "duration_ms")
        calls.append(field)
        return {
            **raw["baseline"],
            "duration_field": field,
            "state": "ready"
            if field != "model_duration_ms" or model_baseline_ready
            else "insufficient",
        }

    monkeypatch.setattr(service, "compute_baseline", compute)
    incident = SimpleNamespace(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        fingerprint="phase-scope",
        first_seen=NOW,
        last_seen=NOW,
        reopen_count=0,
    )
    context = await build_context(Session(), incident)
    assert calls == expected_calls
    assert context.baseline["duration_field"] == expected_calls[-1]
    assert (
        next(n for n in context.topology_nodes if n["id"] == IDS["target-instance"])["source_id"]
        == MODEL_GENERATION
    )
    result = evaluate_r2({**context.__dict__, "events": raw["events"], "tasks": raw["tasks"]})
    assert result["applicable"] is (symptom == "target-container" and model_baseline_ready)
