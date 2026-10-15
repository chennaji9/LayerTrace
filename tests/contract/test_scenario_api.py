"""US2 契约测试：scenario run/verify/restore 与当前拓扑响应。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.contract]


def test_scenario_run_states_contract() -> None:
    from packages.contracts.schemas import ScenarioRunOut

    fields = set(ScenarioRunOut.model_fields)
    assert {
        "id", "scenario_name", "mode", "state", "started_at", "deadline_at",
        "ended_at", "verification_result", "restore_result",
    } <= fields


def test_scenario_run_request_defaults() -> None:
    from packages.contracts.schemas import ScenarioRunRequest

    req = ScenarioRunRequest(mode="replay")
    assert req.timeout_seconds == 180
    assert 1 <= req.timeout_seconds <= 900


def test_topology_response_contract() -> None:
    from packages.contracts.schemas import TopologyResponse

    fields = set(TopologyResponse.model_fields)
    assert {"at", "nodes", "edges", "truncated"} == fields


def test_scenario_name_pattern() -> None:
    from apps.control_plane.api.scenarios import SCENARIO_NAME_RE

    assert SCENARIO_NAME_RE.match("story-oom")
    assert SCENARIO_NAME_RE.match("a1")
    assert not SCENARIO_NAME_RE.match("Story OOM")
    assert not SCENARIO_NAME_RE.match("-bad")
    assert not SCENARIO_NAME_RE.match("x" * 65)
