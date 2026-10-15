"""Probe health output excludes another project's VM and installation identity."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from apps.control_plane.api.probes import register_probe_core
from apps.control_plane.config import get_settings
from apps.control_plane.integrations import status_service

pytestmark = [pytest.mark.integration]


async def test_status_has_only_project_owned_probes(db_session, monkeypatch):
    monkeypatch.setenv("PROBE_REGISTRATION_SECRET", "status-scope-test-registration")
    monkeypatch.setenv("ZSVIRT_ENABLED", "false")
    get_settings.cache_clear()
    project_a, project_b = uuid.uuid4(), uuid.uuid4()

    async def backend_health(_session):
        return {"state": "healthy", "last_success_at": datetime.now(UTC)}

    monkeypatch.setattr(status_service, "check_postgres", backend_health)
    monkeypatch.setattr(status_service, "check_prometheus", backend_health)
    try:
        own = await register_probe_core(
            db_session, installation_id="scope-own", vm_source_id="scope-vm-own",
            profile="lightweight", project_id=project_a,
            provided_registration_token="status-scope-test-registration",
        )
        foreign = await register_probe_core(
            db_session, installation_id="scope-foreign", vm_source_id="scope-vm-foreign",
            profile="lightweight", project_id=project_b,
            provided_registration_token="status-scope-test-registration",
        )
        summary = await status_service.summarize(db_session, project_id=project_a)
        probes = [source for source in summary["sources"] if source["type"] == "probe"]
        assert [source["instance"] for source in probes] == [own["probe_id"]]
        assert all(source["instance"] != foreign["probe_id"] for source in probes)
        assert probes[0]["capabilities"]["installation_id"] == "scope-own"
        assert not any("credential" in key for key in probes[0]["capabilities"])
    finally:
        get_settings.cache_clear()
