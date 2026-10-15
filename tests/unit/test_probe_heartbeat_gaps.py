"""Gap-bearing heartbeats use JSON-safe timestamps at the persistence boundary."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from apps.control_plane.api.errors import register_exception_handlers
from apps.control_plane.api.probes import _hash_credential, router
from apps.control_plane.db import get_session
from apps.control_plane.models import SourceStatus

pytestmark = [pytest.mark.unit]


class JsonBindingSession:
    """Exercise SQLAlchemy's real JSONB binder without a database connection."""

    def __init__(self, status: SourceStatus):
        self.status = status
        self.flush_count = 0
        self.persisted_gaps = None

    async def execute(self, _statement):
        return self

    def scalar_one_or_none(self):
        return self.status

    async def flush(self):
        bind = SourceStatus.__table__.c.data_gaps.type.bind_processor(postgresql.dialect())
        self.persisted_gaps = json.loads(bind(self.status.data_gaps))
        self.flush_count += 1


def heartbeat_app():
    probe_id = uuid.uuid4()
    credential = "offline-heartbeat-test-credential"
    old_gap = {"start": "2026-10-14T18:00:00Z", "end": "2026-10-14T18:00:15Z"}
    status = SourceStatus(
        id=probe_id,
        source_type="probe",
        source_instance=str(probe_id),
        state="stale",
        last_success_at=datetime.now(UTC) - timedelta(minutes=5),
        capabilities={
            "credential_sha256": _hash_credential(credential),
            "credential_expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        },
        dropped_count=0,
        data_gaps=[old_gap],
    )
    session = JsonBindingSession(status)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router)

    async def offline_session():
        yield session

    app.dependency_overrides[get_session] = offline_session
    return app, session, str(probe_id), credential, old_gap


@pytest.mark.parametrize("suffix", ["/heartbeat", ":heartbeat"])
async def test_nonempty_gap_heartbeat_serializes_and_recovers_status(suffix):
    app, session, probe_id, credential, old_gap = heartbeat_app()
    first_success = session.status.last_success_at
    gap = {"start": "2026-10-15T03:36:12.123456+08:00", "end": "2026-10-15T03:41:12.654321+08:00"}
    payload = {
        "observed_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
        "queue_depth": 3,
        "dropped_count": 313,
        "oldest_queued_at": (datetime.now(UTC) - timedelta(seconds=20)).isoformat(),
        "gaps": [gap],
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://offline.test") as client:
        response = await client.post(
            f"/api/v1/probes/{probe_id}{suffix}",
            json=payload,
            headers={"Authorization": "Bearer " + credential},
        )
        assert response.status_code == 204
        assert response.content == b""
        assert session.flush_count == 1
        assert session.status.state == "healthy"
        assert session.status.last_success_at > first_success
        assert session.status.dropped_count == 313
        assert session.status.queue_lag_seconds >= 20
        assert session.status.capabilities["queue_depth"] == 3
        assert session.persisted_gaps[0] == old_gap
        for key in ("start", "end"):
            persisted = session.persisted_gaps[1][key]
            assert isinstance(persisted, str)
            assert datetime.fromisoformat(persisted.replace("Z", "+00:00")) == datetime.fromisoformat(gap[key])

        # An empty later heartbeat must keep recorded loss intervals and counters.
        recorded = session.persisted_gaps
        response = await client.post(
            f"/api/v1/probes/{probe_id}{suffix}",
            json={**payload, "gaps": [], "queue_depth": 0, "oldest_queued_at": None},
            headers={"Authorization": "Bearer " + credential},
        )
        assert response.status_code == 204
        assert session.flush_count == 2
        assert session.persisted_gaps == recorded
        assert session.status.queue_lag_seconds is None
        assert session.status.dropped_count == 313


async def test_gap_heartbeat_invalid_credential_does_not_flush():
    app, session, probe_id, _credential, old_gap = heartbeat_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://offline.test") as client:
        response = await client.post(
            f"/api/v1/probes/{probe_id}/heartbeat",
            json={"observed_at": datetime.now(UTC).isoformat(), "queue_depth": 0, "dropped_count": 313,
                  "gaps": [{"start": "2026-10-14T19:36:12Z", "end": "2026-10-14T19:41:12Z"}]},
            headers={"Authorization": "Bearer invalid-offline-credential"},
        )
    assert response.status_code == 403
    assert session.flush_count == 0
    assert session.status.state == "stale"
    assert session.status.data_gaps == [old_gap]
