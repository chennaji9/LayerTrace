"""US6 契约/单元测试：lab 安全（prod 拒绝、测试标签、超时、action 白名单）。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.contract]


@pytest.fixture(autouse=True)
def _fresh_settings():
    from apps.control_plane.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _settings(monkeypatch_values: dict):
    from apps.control_plane.config import Settings

    return Settings(**{"mode": "lab", "lab_enabled": True, **monkeypatch_values})


def test_prod_mode_rejects_lab(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("LAB_ENABLED", "false")
    from apps.control_plane.scenarios.authorization import authorize_lab_run

    decision = authorize_lab_run(
        mode="lab",
        target_refs=[{"labels": {"layertrace-test": "true"}}],
        timeout_seconds=60,
        actions=[{"operation": "set_memory_limit", "target_ref": "x"}],
    )
    assert not decision.allowed
    assert "生产" in decision.reason or "lab" in decision.reason.lower()


def test_lab_requires_test_label(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "lab")
    monkeypatch.setenv("LAB_ENABLED", "true")
    from apps.control_plane.scenarios.authorization import authorize_lab_run

    decision = authorize_lab_run(
        mode="lab",
        target_refs=[{"labels": {}}],
        timeout_seconds=60,
        actions=[{"operation": "set_memory_limit", "target_ref": "x"}],
    )
    assert not decision.allowed
    assert "测试标签" in decision.reason


def test_lab_timeout_capped(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "lab")
    monkeypatch.setenv("LAB_ENABLED", "true")
    from apps.control_plane.scenarios.authorization import authorize_lab_run

    decision = authorize_lab_run(
        mode="lab",
        target_refs=[{"labels": {"layertrace-test": "true"}}],
        timeout_seconds=901,
        actions=[],
    )
    assert not decision.allowed
    assert "900" in decision.reason


def test_lab_action_whitelist(monkeypatch) -> None:
    monkeypatch.setenv("MODE", "lab")
    monkeypatch.setenv("LAB_ENABLED", "true")
    from apps.control_plane.scenarios.authorization import authorize_lab_run

    ok = authorize_lab_run(
        mode="lab",
        target_refs=[{"labels": {"layertrace-test": "true"}}],
        timeout_seconds=60,
        actions=[{"operation": "start_cpu_load", "target_ref": "vm"}],
    )
    assert ok.allowed
    bad = authorize_lab_run(
        mode="lab",
        target_refs=[{"labels": {"layertrace-test": "true"}}],
        timeout_seconds=60,
        actions=[{"operation": "rm -rf /", "target_ref": "vm"}],
    )
    assert not bad.allowed


def test_lab_mode_not_default() -> None:
    from apps.control_plane.config import Settings

    settings = Settings()
    assert settings.mode == "prod"
    assert settings.lab_enabled is False
