"""Failed server revocation cannot stop collectors or destroy local credentials."""

from __future__ import annotations

import json
import uuid
from argparse import Namespace

import pytest

from scripts import uninstall as module
from scripts.uninstall import API, UninstallError


def test_server_success_still_requires_old_credential_to_be_rejected(monkeypatch):
    api = API("http://127.0.0.1:8000", "administrator-test-token")
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return 204, {}

    monkeypatch.setattr(api, "request", request)
    with pytest.raises(UninstallError, match="not confirmed rejected"):
        api.revoke(str(uuid.uuid4()), old_credential="old-probe-credential")
    assert calls[0][1].endswith(":revoke")
    assert calls[1][1].endswith(":heartbeat")
    assert calls[1][2]["token"] == "old-probe-credential"


def test_exported_credential_cannot_revoke_another_selected_vm(monkeypatch):
    api = API("http://127.0.0.1:8000", "administrator-test-token")
    probe_id = str(uuid.uuid4())
    monkeypatch.setattr(api, "request", lambda *args, **kwargs: (200, {"sources": [{
        "type": "probe", "instance": probe_id,
        "capabilities": {"vm_source_id": "different-vm", "installation_id": "selected-installation"},
    }]}))
    with pytest.raises(UninstallError, match="does not match"):
        api.assert_probe_scope(probe_id, vm_external_id="selected-vm", installation_id="selected-installation")


@pytest.mark.parametrize("url", ["http://192.0.2.2:8000", "https://user:pass@example.com", "ftp://127.0.0.1"])
def test_revocation_rejects_insecure_or_credential_embedded_url(url):
    with pytest.raises(UninstallError):
        API(url, "administrator-test-token")


@pytest.mark.parametrize("revoke_fails", [True, False])
@pytest.mark.parametrize("is_running", [True, False])
def test_vm_uninstall_order_and_credential_preservation(tmp_path, monkeypatch, revoke_fails, is_running):
    env_file = tmp_path / ".env"
    original = "LAYERTRACE_API=http://127.0.0.1:18000\nLAYERTRACE_TOKEN=administrator-test-token\nPROBE_REGISTRATION_SECRET=register-test-secret\nLAYERTRACE_MODEL_DIRECTORY=/opt/model\nPROBE_VM_EXTERNAL_ID=selected-vm\nPROBE_INSTALLATION_ID=selected-installation\n"
    env_file.write_text(original)
    credential_file = tmp_path / "probe-credential.json"
    credential_file.write_text(json.dumps({"probe_id": str(uuid.uuid4()), "credential": "old-probe-credential"}))
    operations = []

    class FakeAPI:
        def __init__(self, *args, **kwargs):
            pass

        def revoke(self, probe_id, **kwargs):
            operations.append("revoke")
            if revoke_fails:
                raise UninstallError("Server revocation returned HTTP 403; stopping was refused")

        def assert_probe_scope(self, probe_id, **kwargs):
            assert kwargs == {"vm_external_id": "selected-vm", "installation_id": "selected-installation"}
            operations.append("identity")

    def run(command):
        for action in ("config", "ps", "pause", "unpause", "down", "run"):
            if action in command:
                operations.append(action)
                break
        if "run" in command:
            assert "/var/lib/layertrace/probe-credential.json" in command[-1]
            assert ".unlink(missing_ok=True)" in command[-1]
            assert "rm -rf" not in command[-1]
        return "vm-probe\n" if "ps" in command and is_running else ""

    monkeypatch.setattr(module, "API", FakeAPI)
    monkeypatch.setattr(module, "run", run)
    args = Namespace(target="vm", env_file=str(env_file), api_url=None, ca_file=None, compose_overlay=[], credential_file=str(credential_file))
    if revoke_fails:
        with pytest.raises(UninstallError, match="HTTP 403"):
            module.uninstall(args)
        assert operations == ["config", "identity", "ps", *(["pause"] if is_running else []), "revoke", *(["unpause"] if is_running else [])]
        assert credential_file.exists()
        assert env_file.read_text() == original
    else:
        result = module.uninstall(args)
        assert operations == ["config", "identity", "ps", *(["pause"] if is_running else []), "revoke", "down", "run", "down"]
        assert result["data_volumes_retained"]
        assert not credential_file.exists()
        values = module.load_env(env_file)
        assert values["LAYERTRACE_TOKEN"] == values["PROBE_REGISTRATION_SECRET"] == ""
        assert values["LAYERTRACE_MODEL_DIRECTORY"] == "/opt/model"
