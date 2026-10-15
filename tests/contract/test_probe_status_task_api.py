"""US4 契约测试：探针注册/心跳、status、tasks、workloads API。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.contract]


def test_probe_registration_contract() -> None:
    from packages.contracts.schemas import ProbeRegistration, ProbeRegistrationResult

    fields = set(ProbeRegistration.model_fields)
    assert {"installation_id", "vm_ref", "profile"} <= fields
    result_fields = set(ProbeRegistrationResult.model_fields)
    assert {"probe_id", "vm_id", "credential", "expires_at"} == result_fields


def test_probe_heartbeat_contract() -> None:
    from packages.contracts.schemas import ProbeHeartbeat

    fields = set(ProbeHeartbeat.model_fields)
    assert {"observed_at", "queue_depth", "dropped_count"} <= fields
    assert "clock_offset_seconds" in fields and "gaps" in fields


def test_workload_contract() -> None:
    from packages.contracts.schemas import WorkloadOut

    fields = set(WorkloadOut.model_fields)
    assert {"container_id", "state", "data_state", "limits", "metrics", "latency_baseline", "provenance"} <= fields


def test_latency_baseline_contract() -> None:
    from packages.contracts.schemas import LatencyBaseline

    fields = set(LatencyBaseline.model_fields)
    assert {"state", "window_seconds", "sample_count", "multiplier"} <= fields
    assert "agent_timeout_ms" in fields


def test_task_run_out_contract() -> None:
    from packages.contracts.schemas import TaskRunOut

    fields = set(TaskRunOut.model_fields)
    assert {"duration_ms", "model_duration_ms", "tool_duration_ms", "status_provenance"} <= fields
