"""Exit-code and object-scope counterexamples for process termination."""

from apps.control_plane.diagnosis.evaluators.r0_process_exit import build
from apps.control_plane.diagnosis.evaluators.r1_oom import build as r1
from tests.unit.diagnosis.test_r1_oom import (
    ev_exit137_only,
    ev_oomkilled,
    make_context,
    task_failed,
)


def test_exit137_observed_failure_is_process_exit_without_memory_claim():
    ctx = make_context(events=[ev_exit137_only()], tasks=[task_failed()])
    result = build().evaluate(ctx)
    assert result.applicable and result.support_level == "high"
    assert result.detail["exit_observations"][0]["exit_code"] == 137
    assert result.detail["memory_cause_confirmed"] is False
    assert not r1().evaluate(ctx).applicable
    assert "OOM" in " ".join(result.missing)


def test_exit_without_client_observation_is_medium():
    ctx = make_context(events=[ev_exit137_only()], tasks=[task_failed(False)])
    assert build().evaluate(ctx).support_level == "medium"


def test_normal_exit_or_sibling_exit_is_not_r0():
    normal = ev_exit137_only()
    normal["payload"] = {"exit_code": 0}
    assert not build().evaluate(make_context(events=[normal], tasks=[task_failed()])).applicable
    sibling = ev_exit137_only()
    sibling["resource_id"] = "other-container"
    assert not build().evaluate(make_context(events=[sibling], tasks=[task_failed()])).applicable


def test_nearby_trusted_oom_uses_r1_and_does_not_duplicate_r0():
    ctx = make_context(events=[ev_exit137_only(), ev_oomkilled()], tasks=[task_failed()])
    assert not build().evaluate(ctx).applicable
    assert r1().evaluate(ctx).applicable
