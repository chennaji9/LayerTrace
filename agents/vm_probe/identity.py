"""Persist and proactively renew the short-lived probe credential."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx


class ProbeIdentity:
    def __init__(self, config) -> None:
        self.config = config
        self.probe_id = self.vm_id = self.credential = self.expires_at = None

    def load(self) -> bool:
        try:
            data = json.loads(Path(self.config.credential_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        for field in ("probe_id", "vm_id", "credential", "expires_at"):
            setattr(self, field, data.get(field))
        return bool(self.probe_id and self.credential and self.expires_at)

    def expired(self, *, margin_seconds: int = 0) -> bool:
        if not self.expires_at:
            return True
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            return expires <= datetime.now(UTC) + timedelta(seconds=margin_seconds)
        except ValueError:
            return True

    def register(self) -> None:
        with httpx.Client(verify=self.config.tls_ca, timeout=20) as client:
            res = client.post(
                f"{self.config.api_base}/api/v1/probes/register",
                json={
                    "installation_id": self.config.installation_id,
                    "vm_ref": {"namespace": "zsvirt.vm", "external_id": self.config.vm_external_id},
                    "profile": self.config.profile,
                    "capabilities": {
                        "docker_proxy": True,
                        "persistent_outbox": True,
                        "metrics_pull": True,
                        "event_time_nano": True,
                    },
                },
                headers={"Authorization": f"Bearer {self.config.registration_token}"},
            )
        res.raise_for_status()
        data = res.json()
        for field in ("probe_id", "vm_id", "credential", "expires_at"):
            setattr(self, field, data[field])
        self.save()

    def save(self) -> None:
        path = Path(self.config.credential_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(
                {
                    key: getattr(self, key)
                    for key in ("probe_id", "vm_id", "credential", "expires_at")
                }
            ),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)

    def auth_headers(self) -> dict:
        if not self.credential:
            raise RuntimeError("Probe has no credential")
        return {"Authorization": f"Bearer {self.credential}"}
