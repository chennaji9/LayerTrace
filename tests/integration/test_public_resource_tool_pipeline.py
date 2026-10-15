"""Public task/model/tool dependency chain distinguishes quota and tool failures."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

pytestmark = [pytest.mark.integration]


async def test_quota_and_tool_diagnoses_follow_dependency_edges(
    test_database_url, clean_db, monkeypatch
):
    from apps.control_plane.config import get_settings
    from apps.control_plane.db import dispose_engine
    from apps.control_plane.main import create_app
    from apps.control_plane.worker import job_diagnosis_sweep

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("API_TOKENS", "test-project-token")
    monkeypatch.setenv("PROBE_REGISTRATION_SECRET", "test-collector-secret")
    monkeypatch.setenv("ZSVIRT_ENABLED", "false")
    get_settings.cache_clear()
    await dispose_engine()
    now = datetime.now(UTC)
    started = (now - timedelta(minutes=10)).isoformat()
    ids = {"agent": "a" * 64, "model": "b" * 64, "tool": "c" * 64}
    instances = {key: f"{value}@{started}" for key, value in ids.items()}
    auth = {"Authorization": "Bearer test-project-token"}
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 1234)),
        base_url="http://localhost",
    ) as client:
        collector = (
            await client.post(
                "/api/v1/probes:register",
                headers={"Authorization": "Bearer test-collector-secret"},
                json={
                    "installation_id": "dependency-probe",
                    "vm_ref": {"namespace": "zsvirt.vm", "external_id": "dependency-vm"},
                    "profile": "lightweight",
                },
            )
        ).json()
        credential = {"Authorization": f"Bearer {collector['credential']}"}
        inventory = {
            "observed_at": now.isoformat(),
            "containers": [
                {
                    "container_id": value,
                    "name": name,
                    "service_name": name,
                    "instance_id": instances[name],
                    "started_at": started,
                    "state": "running",
                    "limits": {"cpu_quota": 200000, "cpu_period_us": 100000},
                }
                for name, value in ids.items()
            ],
            "dependencies": [
                {"from_instance_id": instances["agent"], "to_instance_id": instances["model"]},
                {"from_instance_id": instances["agent"], "to_instance_id": instances["tool"]},
            ],
        }
        response = await client.post(
            f"/api/v1/probes/{collector['probe_id']}/inventory", headers=credential, json=inventory
        )
        assert response.status_code == 200, response.text
        facts = []

        def fact(kind, at, target, payload, task_id=None, container=False):
            event = {
                "schema_version": "1.0",
                "event_id": f"dep-{len(facts)}-{kind}-{at.timestamp()}",
                "event_type": kind,
                "resource_ref": {
                    "namespace": "docker.container" if container else "otel.service_instance",
                    "external_id": ids[target] if container else instances[target],
                },
                "occurred_at": at.isoformat(),
                "observed_at": at.isoformat(),
                "severity": "error" if "failed" in kind else "info",
                "source": "public-load-client",
                "provenance": "observed",
                "attributes": payload,
            }
            if task_id:
                event["correlation"] = {"task_id": task_id}
            facts.append(event)

        for index in range(35):
            at = now - timedelta(seconds=100 - index)
            task = f"healthy-{index}"
            fact("task.started", at, "agent", {}, task)
            fact(
                "task.succeeded",
                at + timedelta(milliseconds=900),
                "agent",
                {
                    "duration_ms": 900,
                    "model_duration_ms": 800,
                    "tool_duration_ms": 20,
                    "model_instance_id": instances["model"],
                },
                task,
            )
        response = await client.post(
            "/api/v1/events:batch", headers=auth, json={"schema_version": "1.0", "events": facts}
        )
        assert response.status_code == 202
        await job_diagnosis_sweep()
        facts = []
        fact(
            "container.stats.sample",
            now,
            "model",
            {
                "cpu_quota": 10000,
                "cpu_period_us": 100000,
                "cpu_percent": 99,
                "throttled_periods_delta": 45,
                "cpu_periods_delta": 50,
                "throttled_ratio": 0.9,
            },
            container=True,
        )
        for index in range(5):
            task = f"slow-{index}"
            at = now + timedelta(seconds=index * 2)
            fact("task.started", at, "agent", {}, task)
            fact(
                "task.succeeded",
                at + timedelta(seconds=3),
                "agent",
                {
                    "duration_ms": 3200,
                    "model_duration_ms": 3000,
                    "tool_duration_ms": 20,
                    "model_instance_id": instances["model"],
                },
                task,
            )
        response = await client.post(
            "/api/v1/events:batch", headers=auth, json={"schema_version": "1.0", "events": facts}
        )
        assert response.status_code == 202
        await job_diagnosis_sweep()
        incidents = (await client.get("/api/v1/incidents", headers=auth)).json()["items"]
        assert len(incidents) == 1
        quota_detail = (
            await client.get(f"/api/v1/incidents/{incidents[0]['id']}", headers=auth)
        ).json()
        candidate = next(c for c in quota_detail["diagnosis"]["candidates"] if c["rule_id"] == "R2")
        assert candidate["support_level"] == "high"
        assert candidate["detail"]["baseline"]["p95_ms"] == 800
        assert candidate["detail"]["baseline"]["current_p95_ms"] == 3000
        assert candidate["detail"]["baseline"]["quota_us"] == [10000]
        facts = []
        at = now + timedelta(seconds=20)
        fact("task.started", at, "agent", {}, "tool-task")
        fact(
            "tool.failed",
            at + timedelta(milliseconds=500),
            "tool",
            {"http_status": 503, "tool_name": "runbook"},
            "tool-task",
        )
        fact(
            "task.failed",
            at + timedelta(seconds=1),
            "agent",
            {"duration_ms": 1000, "error_class": "tool_http_503"},
            "tool-task",
        )
        fact(
            "container.stats.sample",
            at,
            "tool",
            {"cpu_quota": 100000, "cpu_percent": 0, "throttled_periods_delta": 0},
            container=True,
        )
        response = await client.post(
            "/api/v1/events:batch", headers=auth, json={"schema_version": "1.0", "events": facts}
        )
        assert response.status_code == 202
        await job_diagnosis_sweep()
        detail = (await client.get("/api/v1/tasks/tool-task", headers=auth)).json()
        assert detail["incident_ids"]
        candidate_details = [
            (await client.get(f"/api/v1/incidents/{incident}", headers=auth)).json()
            for incident in detail["incident_ids"]
        ]
        assert any(
            c["rule_id"] == "R3" and c["support_level"] == "high"
            for d in candidate_details
            for c in d["diagnosis"]["candidates"]
        )
    await dispose_engine()
    get_settings.cache_clear()
