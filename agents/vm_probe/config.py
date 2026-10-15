"""Standalone VM probe configuration."""

from __future__ import annotations

import os
from pathlib import Path


class ProbeConfig:
    def __init__(self) -> None:
        self.api_base = os.environ.get("LAYERTRACE_API", "http://127.0.0.1:8000").rstrip("/")
        self.installation_id = os.environ.get("PROBE_INSTALLATION_ID", "")
        self.vm_external_id = os.environ.get("PROBE_VM_EXTERNAL_ID", "")
        self.profile = os.environ.get("PROBE_PROFILE", "lightweight")
        self.registration_token = os.environ.get("PROBE_REGISTRATION_SECRET") or os.environ.get(
            "PROBE_REGISTRATION_TOKEN", ""
        )
        self.state_dir = os.environ.get("PROBE_STATE_DIR", "/var/lib/layertrace")
        self.identity_path = os.environ.get("PROBE_IDENTITY_PATH", "/identity/services.json")
        self.heartbeat_interval = max(1, int(os.environ.get("HEARTBEAT_INTERVAL_SECONDS", "15")))
        self.sample_interval = max(1, int(os.environ.get("SAMPLE_INTERVAL_SECONDS", "5")))
        self.inventory_interval = max(1, int(os.environ.get("INVENTORY_INTERVAL_SECONDS", "10")))
        self.docker_proxy_url = os.environ.get("DOCKER_PROXY_URL", "http://127.0.0.1:2375")
        self.container_label = os.environ.get("PROBE_CONTAINER_LABEL", "layertrace.observe=true")
        self.spool_max_bytes = int(os.environ.get("PROBE_SPOOL_MAX_BYTES", str(100 * 1024 * 1024)))
        self.spool_max_seconds = int(os.environ.get("PROBE_SPOOL_MAX_SECONDS", "600"))
        self.metrics_host = os.environ.get("PROBE_METRICS_HOST", "0.0.0.0")
        self.metrics_port = int(os.environ.get("PROBE_METRICS_PORT", "9108"))
        self.tls_ca = os.environ.get("LAYERTRACE_CA_FILE") or True

    @property
    def credential_path(self) -> str:
        return str(Path(self.state_dir) / "probe-credential.json")

    def validate(self) -> list[str]:
        errors = []
        for name, value in (
            ("PROBE_INSTALLATION_ID", self.installation_id),
            ("PROBE_VM_EXTERNAL_ID", self.vm_external_id),
            ("PROBE_REGISTRATION_SECRET", self.registration_token),
        ):
            if not value:
                errors.append(f"{name} is required")
        if self.profile not in ("lightweight", "standard"):
            errors.append("PROBE_PROFILE must be lightweight or standard")
        return errors
