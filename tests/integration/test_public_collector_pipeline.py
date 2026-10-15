"""Exercise signed collector/public endpoints and the worker, without ORM fact seeds."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = [pytest.mark.integration]


async def test_public_probe_worker_diagnosis_and_scope(test_database_url, clean_db, monkeypatch):
    from apps.control_plane.config import get_settings
    from apps.control_plane.db import dispose_engine
    from apps.control_plane.main import create_app
    from apps.control_plane.worker import job_diagnosis_sweep

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("API_TOKENS", "public-api-test-token")
    monkeypatch.setenv("PROBE_REGISTRATION_SECRET", "public-register-test-secret")
    monkeypatch.setenv("MODE", "lab")
    monkeypatch.setenv("ZSVIRT_ENABLED", "false")
    get_settings.cache_clear()
    await dispose_engine()
    app = create_app()
    now = datetime.now(UTC)
    started = (now - timedelta(minutes=5)).isoformat()
    container = "a" * 64
    instance = f"{container}@{started}"
    user = {"Authorization": "Bearer public-api-test-token"}
    registration = {"Authorization": "Bearer public-register-test-secret"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        bad = await client.post(
            "/api/v1/probes:register",
            json={
                "installation_id": "probe-a",
                "vm_ref": {"namespace": "zsvirt.vm", "external_id": "vm-a"},
                "profile": "lightweight",
            },
        )
        assert bad.status_code == 403
        response = await client.post(
            "/api/v1/probes:register",
            headers=registration,
            json={
                "installation_id": "probe-a",
                "vm_ref": {"namespace": "zsvirt.vm", "external_id": "vm-a"},
                "profile": "lightweight",
            },
        )
        assert response.status_code == 201, response.text
        collector = response.json()
        credential = {"Authorization": f"Bearer {collector['credential']}"}
        inventory = {
            "observed_at": now.isoformat(),
            "containers": [
                {
                    "container_id": container,
                    "name": "agent",
                    "service_name": "agent",
                    "instance_id": instance,
                    "started_at": started,
                    "state": "running",
                    "limits": {"memory_limit_bytes": 536870912},
                }
            ],
        }
        response = await client.post(
            f"/api/v1/probes/{collector['probe_id']}/inventory", headers=credential, json=inventory
        )
        assert response.status_code == 200, response.text
        heartbeat = {"observed_at": now.isoformat(), "queue_depth": 0, "dropped_count": 0}
        response = await client.post(
            f"/api/v1/probes/{collector['probe_id']}:heartbeat", json=heartbeat
        )
        assert response.status_code == 403
        response = await client.post(
            f"/api/v1/probes/{collector['probe_id']}:heartbeat", headers=credential, json=heartbeat
        )
        assert response.status_code == 204, response.text

        def event(kind, second, namespace, external_id, attributes, task=None):
            at = (now + timedelta(seconds=second)).isoformat()
            raw = {
                "schema_version": "1.0",
                "event_id": f"public-{kind}-{second}",
                "event_type": kind,
                "resource_ref": {"namespace": namespace, "external_id": external_id},
                "occurred_at": at,
                "observed_at": at,
                "severity": "error" if kind != "task.started" else "info",
                "source": "probe-test",
                "provenance": "observed",
                "attributes": attributes,
            }
            if task:
                raw["correlation"] = {"task_id": task}
            return raw

        facts = [
            event("task.started", 0, "otel.service_instance", instance, {}, "public-task"),
            event("container.oom", 1, "docker.container", container, {"oom_killed": True}),
            event(
                "task.failed",
                2,
                "otel.service_instance",
                instance,
                {"observer": "client", "duration_ms": 2000},
                "public-task",
            ),
        ]
        batch = {"schema_version": "1.0", "events": facts[:2]}
        response = await client.post("/api/v1/events:batch", headers=credential, json=batch)
        assert response.status_code == 202, response.text
        assert response.json()["accepted"] == 2
        await job_diagnosis_sweep()
        response = await client.get("/api/v1/tasks?task_id=public-task", headers=user)
        assert response.status_code == 200, response.text
        task = response.json()["items"][0]
        assert task["status"] == "interrupted" and task["status_provenance"] == "inferred"
        incidents = (await client.get("/api/v1/incidents", headers=user)).json()["items"]
        assert len(incidents) == 1
        detail = (await client.get(f"/api/v1/incidents/{incidents[0]['id']}", headers=user)).json()
        assert any(
            c["rule_id"] == "R1" and c["support_level"] == "medium"
            for c in detail["diagnosis"]["candidates"]
        )
        initial_revision = detail["diagnosis"]["revision"]
        # A later input at the same original time revises evidence; incident last_seen need not change.
        response = await client.post(
            "/api/v1/events:batch",
            headers=credential,
            json={"schema_version": "1.0", "events": facts[2:]},
        )
        assert response.status_code == 202
        await job_diagnosis_sweep()
        detail = (await client.get(f"/api/v1/incidents/{incidents[0]['id']}", headers=user)).json()
        assert any(
            c["rule_id"] == "R1" and c["support_level"] == "high"
            for c in detail["diagnosis"]["candidates"]
        )
        assert detail["diagnosis"]["revision"] > initial_revision
        assert detail["evidence"]
        revision = detail["diagnosis"]["revision"]
        duplicate = await client.post(
            "/api/v1/events:batch",
            headers=credential,
            json={"schema_version": "1.0", "events": facts},
        )
        assert duplicate.json()["duplicate"] == 3
        await job_diagnosis_sweep()
        detail = (await client.get(f"/api/v1/incidents/{incidents[0]['id']}", headers=user)).json()
        assert detail["diagnosis"]["revision"] == revision
        # A collector cannot write another VM's resource or fake its heartbeat.
        b = (
            await client.post(
                "/api/v1/probes:register",
                headers=registration,
                json={
                    "installation_id": "probe-b",
                    "vm_ref": {"namespace": "zsvirt.vm", "external_id": "vm-b"},
                    "profile": "lightweight",
                },
            )
        ).json()
        denied = await client.post(
            f"/api/v1/probes/{b['probe_id']}:heartbeat", headers=credential, json=heartbeat
        )
        assert denied.status_code == 403
        b_inventory = {
            "observed_at": now.isoformat(),
            "containers": [
                {
                    "container_id": "b" * 64,
                    "name": "other",
                    "started_at": started,
                    "state": "running",
                }
            ],
        }
        response = await client.post(
            f"/api/v1/probes/{b['probe_id']}/inventory",
            headers={"Authorization": f"Bearer {b['credential']}"},
            json=b_inventory,
        )
        assert response.status_code == 200
        other = {
            "schema_version": "1.0",
            "events": [
                event("container.oom", 3, "docker.container", "b" * 64, {"oom_killed": True})
            ],
        }
        assert (
            await client.post("/api/v1/events:batch", headers=credential, json=other)
        ).status_code == 403
        revoke_path = f"/api/v1/probes/{collector['probe_id']}:revoke"
        assert (await client.post(revoke_path, headers=credential)).status_code == 403
        assert (await client.post(revoke_path, headers=user)).status_code == 204
        assert (
            await client.post(
                f"/api/v1/probes/{collector['probe_id']}:heartbeat",
                headers=credential,
                json=heartbeat,
            )
        ).status_code == 403
        assert (
            await client.post("/api/v1/events:batch", headers=credential, json=batch)
        ).status_code == 401
        assert (
            await client.post(
                f"/api/v1/probes/{collector['probe_id']}/inventory",
                headers=credential,
                json=inventory,
            )
        ).status_code == 401
        retry_registration = await client.post(
            "/api/v1/probes:register",
            headers=registration,
            json={
                "installation_id": "probe-a",
                "vm_ref": {"namespace": "zsvirt.vm", "external_id": "vm-a"},
                "profile": "lightweight",
            },
        )
        assert retry_registration.status_code == 403
        assert retry_registration.json()["code"] == "probe_revoked"
    await dispose_engine()
    get_settings.cache_clear()
