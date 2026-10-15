"""Public keyset pages retain project scope and every tied timestamp exactly once."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.control_plane.api.pagination import encode_cursor
from apps.control_plane.config import get_settings
from apps.control_plane.db import dispose_engine
from apps.control_plane.main import create_app
from apps.control_plane.models import Incident, Resource, TaskRun
from tests.seed_utils import PROJECT, TENANT

pytestmark = [pytest.mark.integration]


@pytest.mark.parametrize("endpoint", ["resources", "workloads", "tasks", "incidents"])
async def test_public_cursor_all_rows_once_and_project_scoped(
    db_session, monkeypatch, test_database_url, endpoint
):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("API_TOKENS", "pagination-test-token")
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("ZSVIRT_ENABLED", "false")
    get_settings.cache_clear()
    await dispose_engine()
    app = create_app()
    now = datetime.now(UTC)
    foreign = uuid.uuid4()
    instance = Resource(
        tenant_id=TENANT,
        project_id=PROJECT,
        kind="service_instance",
        provider="test",
        provider_instance="test",
        source_id="pagination-instance",
        provenance="observed",
        first_seen=now,
        last_seen=now,
    )
    db_session.add(instance)
    await db_session.flush()
    expected = []
    for index in range(210):
        project = PROJECT if index < 205 else foreign
        key = uuid.UUID(int=index + 10)
        at = now - timedelta(seconds=index % 3)
        if endpoint == "tasks":
            row = TaskRun(
                id=key,
                tenant_id=TENANT,
                project_id=project,
                task_id=f"pagination-{index}",
                service_instance_id=instance.id,
                status="succeeded",
                status_provenance="observed",
                started_at=at,
                ended_at=at,
                duration_ms=1000,
            )
        elif endpoint == "incidents":
            row = Incident(
                id=key,
                tenant_id=TENANT,
                project_id=project,
                fingerprint=f"pagination-{index}",
                state="open",
                severity="P3",
                first_seen=at,
                last_seen=at,
                alert_count=1,
                reopen_count=0,
            )
        else:
            row = Resource(
                id=key,
                tenant_id=TENANT,
                project_id=project,
                kind="container",
                provider="test",
                provider_instance="test",
                source_id=f"pagination-{index}",
                attributes={"state": "running"},
                provenance="observed",
                first_seen=at,
                last_seen=at,
            )
        db_session.add(row)
        if project == PROJECT:
            expected.append((at, str(key)))
    await db_session.commit()
    expected_ids = [key for _, key in sorted(expected, reverse=True)]
    headers = {"Authorization": "Bearer pagination-test-token"}
    filters = {"resources": {"kind": "container"}, "workloads": {"state": "running"}}.get(
        endpoint, {}
    )
    params = {"limit": 7, **filters}
    path = f"/api/v1/{endpoint}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        seen, cursors = [], set()
        cursor = None
        for _ in range(40):
            response = await client.get(
                path, headers=headers, params={**params, **({"cursor": cursor} if cursor else {})}
            )
            assert response.status_code == 200, response.text
            data = response.json()
            assert len(data["items"]) <= 7
            seen.extend(row["id"] for row in data["items"])
            if endpoint == "incidents":
                assert data["incident_count"] == 205
            cursor = data["next_cursor"]
            if cursor is None:
                break
            assert cursor not in cursors
            cursors.add(cursor)
        else:
            pytest.fail("Pagination failed to reach the last page")
        assert seen == expected_ids
        assert len(set(seen)) == 205
        for invalid in (
            "not-a-cursor",
            encode_cursor("2026-10-15T01:00:00", str(uuid.uuid4())),
            encode_cursor(now.isoformat(), "invalid-id"),
            "中文",
        ):
            invalid_response = await client.get(
                path, headers=headers, params={**params, "cursor": invalid}
            )
            assert invalid_response.status_code == 400
    await dispose_engine()
    get_settings.cache_clear()
