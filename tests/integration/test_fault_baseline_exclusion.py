"""Successful requests during a causal fault cannot become healthy baseline samples."""

from datetime import UTC, datetime, timedelta

import pytest

from apps.control_plane.config import get_settings
from apps.control_plane.diagnosis.baseline import alert_exclusion_windows, compute_baseline
from apps.control_plane.models import Alert, Incident, TaskRun
from tests.seed_utils import PROJECT, TENANT, seed_resource

pytestmark = [pytest.mark.integration]


async def test_baseline_excludes_fault_and_crossing_requests_in_exact_scope(
    db_session, monkeypatch
):
    monkeypatch.setenv("BASELINE_WINDOW_SECONDS", "900")
    monkeypatch.setenv("INCIDENT_RECOVERY_STABLE_SECONDS", "60")
    get_settings.cache_clear()
    now = datetime.now(UTC)
    first = now - timedelta(minutes=15)
    agent = await seed_resource(
        db_session,
        kind="service_instance",
        provider="test",
        source_id="baseline-agent",
        namespace="test",
        external_id="baseline-agent",
        now=first,
    )
    model = await seed_resource(
        db_session,
        kind="container",
        provider="test",
        source_id="baseline-model",
        namespace="test",
        external_id="baseline-model",
        now=first,
    )
    unrelated = await seed_resource(
        db_session,
        kind="container",
        provider="test",
        source_id="other-model",
        namespace="test",
        external_id="other-model",
        now=first,
    )
    fault_start, fault_end = now - timedelta(minutes=6), now - timedelta(minutes=3)
    for resource, start, end in (
        (model, fault_start, fault_end),
        (unrelated, now - timedelta(minutes=12), now - timedelta(minutes=10)),
    ):
        fingerprint = f"baseline-{resource.id}"
        db_session.add(
            Incident(
                tenant_id=TENANT,
                project_id=PROJECT,
                fingerprint=fingerprint,
                state="resolved",
                severity="P3",
                first_seen=start,
                last_seen=end,
                resolved_at=end + timedelta(seconds=60),
                alert_count=1,
                reopen_count=0,
            )
        )
        db_session.add(
            Alert(
                tenant_id=TENANT,
                project_id=PROJECT,
                resource_id=resource.id,
                fingerprint=fingerprint,
                rule_id="R1",
                rule_version="1.0.0",
                state="resolved",
                severity="warning",
                starts_at=start,
                ends_at=end,
                source="test",
                source_alert_id=fingerprint,
                provenance="observed",
            )
        )
    for index in range(30):
        started = now - timedelta(minutes=12) + timedelta(seconds=index * 2)
        db_session.add(
            TaskRun(
                tenant_id=TENANT,
                project_id=PROJECT,
                task_id=f"healthy-{index}",
                service_instance_id=agent.id,
                started_at=started,
                ended_at=started + timedelta(seconds=1),
                status="succeeded",
                status_provenance="observed",
                duration_ms=1000,
            )
        )
    for index in range(40):
        started = fault_start + timedelta(seconds=index * 2)
        db_session.add(
            TaskRun(
                tenant_id=TENANT,
                project_id=PROJECT,
                task_id=f"fault-succeeded-{index}",
                service_instance_id=agent.id,
                started_at=started,
                ended_at=started + timedelta(seconds=60),
                status="succeeded",
                status_provenance="observed",
                duration_ms=60000,
            )
        )
    db_session.add(
        TaskRun(
            tenant_id=TENANT,
            project_id=PROJECT,
            task_id="crossing-fault",
            service_instance_id=agent.id,
            started_at=fault_start - timedelta(seconds=10),
            ended_at=fault_start + timedelta(seconds=30),
            status="succeeded",
            status_provenance="observed",
            duration_ms=40000,
        )
    )
    await db_session.flush()
    windows = await alert_exclusion_windows(
        db_session, project_id=PROJECT, now=now, resource_ids=[model.id, agent.id]
    )
    assert windows == [(fault_start, fault_end + timedelta(seconds=60))]
    baseline = await compute_baseline(
        db_session,
        project_id=PROJECT,
        now=now,
        service_instance_ids=[agent.id],
        exclude_incident_windows=windows,
    )
    assert baseline["state"] == "ready"
    assert baseline["sample_count"] == 30
    assert baseline["p95_ms"] == 1000
    get_settings.cache_clear()
