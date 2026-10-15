"""US5 单元测试：指纹、派生告警、P1/P2/P3、确认/恢复/重开状态机。"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.unit]

PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_fingerprint_stable_per_project_rule_object() -> None:
    from apps.control_plane.incidents.service import fingerprint_for

    fp1 = fingerprint_for(PROJECT, "R1", "container-1")
    fp2 = fingerprint_for(PROJECT, "R1", "container-1")
    fp3 = fingerprint_for(PROJECT, "R1", "container-2")
    fp4 = fingerprint_for(uuid.UUID("00000000-0000-0000-0000-000000000002"), "R1", "container-1")
    assert fp1 == fp2, "同一稳定故障对象指纹稳定"
    assert fp1 != fp3, "不同故障对象指纹不同"
    assert fp1 != fp4, "不同项目指纹不同"


def test_incident_state_machine() -> None:
    from apps.control_plane.incidents.service import ALLOWED_TRANSITIONS

    assert "acknowledged" in ALLOWED_TRANSITIONS["open"]
    assert "resolved" in ALLOWED_TRANSITIONS["open"]
    assert "resolved" in ALLOWED_TRANSITIONS["acknowledged"]
    assert "open" in ALLOWED_TRANSITIONS["resolved"]  # 重开
    assert "open" not in ALLOWED_TRANSITIONS["acknowledged"]


def test_severity_ordering() -> None:
    from apps.control_plane.incidents.service import SEVERITY_RANK

    assert SEVERITY_RANK["P1"] > SEVERITY_RANK["P2"] > SEVERITY_RANK["P3"]


def test_recovery_requires_all_alerts_resolved_and_stable() -> None:
    from apps.control_plane.incidents.recovery import can_recover

    now_firing = [{"state": "firing", "ends_at": None}]
    assert can_recover(now_firing, stable_since=None, now=None) is False

    all_resolved_recent = [{"state": "resolved", "ends_at": 10}]
    # resolved 但未过稳定窗口（< 60s）
    assert can_recover(all_resolved_recent, stable_since=30, now=50) is False
    # resolved 且稳定超过 60 秒
    assert can_recover(all_resolved_recent, stable_since=0, now=120) is True


def test_recovery_rejects_stale_sources() -> None:
    from apps.control_plane.incidents.recovery import can_recover

    # 采集失联（stale/unavailable）不得视为恢复
    all_resolved = [{"state": "resolved", "ends_at": 100}]
    assert can_recover(all_resolved, stable_since=0, now=200, source_states=["stale"]) is False
    assert can_recover(all_resolved, stable_since=0, now=200, source_states=["unavailable"]) is False
