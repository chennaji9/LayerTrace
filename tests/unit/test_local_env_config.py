"""Generated local configuration aligns credentials and Compose deployment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.configure_local_env import create_env

ROOT = Path(__file__).resolve().parents[2]


def test_random_credentials_consistent_database_url_and_no_overwrite(tmp_path):
    target = tmp_path / ".env"
    create_env(target, ROOT / ".env.example", mode="replay")
    values = dict(line.split("=", 1) for line in target.read_text().splitlines() if "=" in line and not line.lstrip().startswith("#"))
    assert values["MODE"] == "replay"
    assert len(values["POSTGRES_PASSWORD"]) == 48
    assert f":{values['POSTGRES_PASSWORD']}@postgres:5432/" in values["DATABASE_URL"]
    assert len({values[key] for key in ("POSTGRES_PASSWORD", "GRAFANA_ADMIN_PASSWORD", "API_TOKENS", "PROBE_REGISTRATION_SECRET")}) == 4
    assert all(len(values[key]) >= 32 for key in ("GRAFANA_ADMIN_PASSWORD", "API_TOKENS", "PROBE_REGISTRATION_SECRET"))
    assert target.stat().st_mode & 0o777 == 0o600
    original = target.read_bytes()
    with pytest.raises(FileExistsError):
        create_env(target, ROOT / ".env.example")
    assert target.read_bytes() == original


def test_compose_uses_generated_credentials_and_rejects_missing_values(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose is unavailable")
    target = tmp_path / ".env"
    create_env(target, ROOT / ".env.example")
    command = ["docker", "compose", "--env-file", str(target), "-f", str(ROOT / "deploy/compose.yaml"), "config", "--format", "json"]
    environment = {key: value for key, value in os.environ.items() if key not in {"POSTGRES_PASSWORD", "DATABASE_URL", "GRAFANA_ADMIN_PASSWORD", "API_TOKENS", "PROBE_REGISTRATION_SECRET"}}
    result = subprocess.run(command, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, "Generated deployment failed Compose validation"
    parsed = json.loads(result.stdout)
    services = parsed["services"]
    control = services["control-plane"]["environment"]
    password = services["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert f":{password}@postgres:5432/" in control["DATABASE_URL"]
    assert services["worker"]["environment"]["PROBE_REGISTRATION_SECRET"] == control["PROBE_REGISTRATION_SECRET"]
    assert services["migrate"]["environment"]["API_TOKENS"] == control["API_TOKENS"]
    assert services["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"] != "layertrace"
    assert "prom/prometheus:v3.5.0@sha256:" in services["prometheus"]["image"]
    assert any(volume.get("target") == "/etc/prometheus/targets" and volume.get("read_only") for volume in services["prometheus"]["volumes"])
    empty = tmp_path / "empty.env"
    empty.write_text("MODE=prod\n")
    missing = subprocess.run(["docker", "compose", "--env-file", str(empty), "-f", str(ROOT / "deploy/compose.yaml"), "config", "--quiet"], env=environment, capture_output=True, text=True)
    assert missing.returncode != 0
