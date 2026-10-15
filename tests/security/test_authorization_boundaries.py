"""US7 安全测试：授权边界（T127 上半）—— 越权写入、伪造转发头、prod lab 拒绝。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.security]


def test_transport_security_rejects_plaintext_off_loopback() -> None:
    """跨主机明文连接被拒绝（426）。"""
    from tests.integration.test_foundation import _run_middleware

    result, _ = _run_middleware("198.51.100.7", "http", [])
    assert result["http.response.start"]["status"] == 426


def test_transport_security_rejects_spoofed_forwarded_proto() -> None:
    from tests.integration.test_foundation import _run_middleware

    result, _ = _run_middleware("198.51.100.7", "http", [(b"x-forwarded-proto", b"https")])
    assert result["http.response.start"]["status"] == 426


def test_transport_security_accepts_explicit_trusted_proxy(monkeypatch) -> None:
    from apps.control_plane.config import get_settings
    from tests.integration.test_foundation import _run_middleware

    monkeypatch.setenv("TRUSTED_PROXIES", "172.20.0.9/32")
    get_settings.cache_clear()
    try:
        result, captured = _run_middleware(
            "172.20.0.9", "http", [(b"x-forwarded-proto", b"https")]
        )
        assert result["http.response.start"]["status"] == 200
        assert captured["scheme"] == "https"
        result, _ = _run_middleware(
            "172.20.0.10", "http", [(b"x-forwarded-proto", b"https")]
        )
        assert result["http.response.start"]["status"] == 426
    finally:
        get_settings.cache_clear()


def test_docker_client_denies_write_operations() -> None:
    """受限 Docker 客户端在客户端层拒绝 create/exec/delete。"""
    from agents.vm_probe.docker_client import DockerProxyClient

    client = DockerProxyClient("http://127.0.0.1:2375")
    for op in ("create", "exec", "delete", "kill"):
        with pytest.raises(PermissionError):
            client._check(op)


def test_prod_lab_operations_rejected() -> None:
    from apps.control_plane.config import get_settings
    from apps.control_plane.scenarios.authorization import authorize_lab_run

    get_settings.cache_clear()
    import os

    os.environ["MODE"] = "prod"
    os.environ["LAB_ENABLED"] = "false"
    try:
        decision = authorize_lab_run(
            mode="lab",
            target_refs=[{"labels": {"layertrace-test": "true"}}],
            timeout_seconds=60,
            actions=[],
        )
        assert not decision.allowed
    finally:
        os.environ.pop("MODE", None)
        os.environ.pop("LAB_ENABLED", None)
        get_settings.cache_clear()


def test_scenario_name_pattern_strict() -> None:
    from apps.control_plane.integrations.scenario_service import SCENARIO_NAME_RE

    for bad in ("../etc-passwd", "A1 UPPER", "a b", "x", "-leading"):
        assert not SCENARIO_NAME_RE.match(bad), bad
