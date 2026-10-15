"""Evidence lookups and isolated topology roots cannot expose foreign projects."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from apps.control_plane.config import get_settings
from apps.control_plane.db import dispose_engine
from apps.control_plane.main import create_app
from apps.control_plane.models import Evidence, Resource
from tests.seed_utils import PROJECT, TENANT

pytestmark = [pytest.mark.integration]


async def test_evidence_and_topology_project_scope(db_session, monkeypatch, test_database_url):
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("API_TOKENS", "scoped-evidence-test-token")
    monkeypatch.setenv("MODE", "prod")
    monkeypatch.setenv("ZSVIRT_ENABLED", "false")
    get_settings.cache_clear()
    await dispose_engine()
    app = create_app()
    now = datetime.now(UTC)
    records = []
    for index, project_id in enumerate((PROJECT, uuid.uuid4())):
        resource = Resource(
            tenant_id=TENANT,
            project_id=project_id,
            kind="container",
            provider="test",
            provider_instance="test",
            source_id=f"scope-{index}",
            provenance="observed",
            first_seen=now,
            last_seen=now,
        )
        db_session.add(resource)
        await db_session.flush()
        evidence = Evidence(
            tenant_id=TENANT,
            project_id=project_id,
            kind="event",
            resource_id=resource.id,
            range_start=now,
            range_end=now + timedelta(seconds=1),
            source_ref=f"event:scope-{index}",
            summary={"cpu_quota": 15000},
            query_parameters={"event_type": "container.stats.sample"},
            provenance="observed",
            source_expired=True,
        )
        db_session.add(evidence)
        await db_session.flush()
        records.append((resource, evidence))
    await db_session.commit()
    own_resource, own_evidence = records[0]
    foreign_resource, foreign_evidence = records[1]
    headers = {"Authorization": "Bearer scoped-evidence-test-token"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        unauthenticated = await client.get(f"/api/v1/evidence/{own_evidence.id}")
        assert unauthenticated.status_code == 401
        own = await client.get(f"/api/v1/evidence/{own_evidence.id}", headers=headers)
        assert own.status_code == 200
        body = own.json()
        assert set(body) == {
            "id",
            "kind",
            "resource_id",
            "range_start",
            "range_end",
            "source_ref",
            "summary",
            "query_parameters",
            "provenance",
            "source_expired",
        }
        assert body["resource_id"] == str(own_resource.id)
        assert body["summary"] == {"cpu_quota": 15000}
        assert body["query_parameters"] == {"event_type": "container.stats.sample"}
        assert body["source_expired"] is True
        assert datetime.fromisoformat(body["range_start"]) == now
        assert datetime.fromisoformat(body["range_end"]) == now + timedelta(seconds=1)
        for key in (foreign_evidence.id, uuid.uuid4()):
            hidden = await client.get(f"/api/v1/evidence/{key}", headers=headers)
            assert hidden.status_code == 404
            assert hidden.json()["code"] == "not_found"
        own_graph = await client.get(
            "/api/v1/topology", params={"root_id": str(own_resource.id)}, headers=headers
        )
        assert own_graph.status_code == 200
        assert [node["id"] for node in own_graph.json()["nodes"]] == [str(own_resource.id)]
        foreign_graph = await client.get(
            "/api/v1/topology", params={"root_id": str(foreign_resource.id)}, headers=headers
        )
        assert foreign_graph.status_code == 200
        assert foreign_graph.json()["nodes"] == []
        assert foreign_graph.json()["edges"] == []
    await dispose_engine()
    get_settings.cache_clear()
