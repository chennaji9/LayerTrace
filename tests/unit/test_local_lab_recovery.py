"""Cold-boot readiness must not turn delayed platform startup into forced VM actions."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def runtime(monkeypatch):
    source = Path(__file__).parents[2] / "deploy/local-lab/runtime.py"
    spec = importlib.util.spec_from_file_location("local_lab_recovery_tests", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clock = [0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    lab = object.__new__(module.Runtime)
    lab.cfg = {"boot_timeout_seconds": 6, "platform_rest_url": "http://127.0.0.1:8880/zstack/v1"}
    lab.vm = "1" * 32
    return lab


def test_api_wait_tolerates_connection_reset_and_startup_response(runtime):
    replies = iter([ConnectionResetError(), (503, {}, {}), (200, {"version": "1.0.0"}, {})])

    def request(*args):
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result

    runtime.request = request
    runtime.wait_platform()


def test_api_wait_is_bounded(runtime):
    runtime.request = lambda *args: (503, {}, {})
    with pytest.raises(RuntimeError, match="boot timeout"):
        runtime.wait_platform()


def test_unknown_vm_is_requeried_before_one_start(runtime):
    states = iter(["Unknown", "Stopped"])
    writes = []

    def call(method, path, body=None):
        if method == "GET":
            return {"inventories": [{"uuid": runtime.vm, "state": next(states)}]}
        writes.append((path, body))
        return {}

    runtime.platform_call = call
    runtime.ensure_business_vm()
    assert writes == [(f"/vm-instances/{runtime.vm}/actions", {"startVmInstance": {}})]


def test_transitional_vm_timeout_never_forces_start(runtime):
    def call(method, path, body=None):
        assert method == "GET"
        return {"inventories": [{"uuid": runtime.vm, "state": "Starting"}]}

    runtime.platform_call = call
    with pytest.raises(RuntimeError, match="no force operation"):
        runtime.ensure_business_vm()


def test_absent_vm_is_never_replaced(runtime):
    runtime.platform_call = lambda *args: {"inventories": []}
    with pytest.raises(RuntimeError, match="never creates a replacement"):
        runtime.ensure_business_vm()


def test_unexpected_vm_state_is_not_retried_or_started(runtime):
    runtime.platform_call = lambda *args: {"inventory": {"uuid": runtime.vm, "state": "Destroyed"}}
    with pytest.raises(RuntimeError, match="no automatic force operation"):
        runtime.ensure_business_vm()
