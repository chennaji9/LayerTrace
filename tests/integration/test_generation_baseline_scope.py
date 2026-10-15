"""Real temporal graph and SQL baseline exclude retired/previous dependency samples."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from apps.control_plane.config import get_settings
from apps.control_plane.diagnosis.baseline import alert_exclusion_windows
from apps.control_plane.diagnosis.context import build_context
from apps.control_plane.diagnosis.service import generate_revision
from apps.control_plane.incidents.event_alerts import process_event_alerts
from apps.control_plane.inventory.temporal_graph import effective_topology
from apps.control_plane.models import Alert, Event, Incident, Relationship, TaskRun
from tests.seed_utils import PROJECT, TENANT, seed_relationship, seed_resource

pytestmark = [pytest.mark.integration]


@pytest.mark.parametrize("current_samples", [29, 30])
@pytest.mark.parametrize("reopened", [False, True])
async def test_new_model_requires_current_dependency_samples(
    db_session, monkeypatch, current_samples, reopened
):
    session = db_session
    monkeypatch.setenv("BASELINE_WINDOW_SECONDS", "3600")
    monkeypatch.setenv("INCIDENT_RECOVERY_STABLE_SECONDS", "60")
    get_settings.cache_clear()
    fault = datetime.now(UTC)
    before = fault - timedelta(minutes=45)
    replacement = fault - timedelta(minutes=10)
    resources = {}
    for name, kind in (
        ("vm", "vm"),
        ("model-old", "container"),
        ("model-new", "container"),
        ("agent-old", "container"),
        ("agent-current", "container"),
        ("model-old-instance", "service_instance"),
        ("model-new-instance", "service_instance"),
        ("agent-old-instance", "service_instance"),
        ("agent-current-instance", "service_instance"),
        ("model-service", "service"),
        ("agent-service", "service"),
    ):
        resources[name] = await seed_resource(
            session,
            kind=kind,
            provider="test",
            source_id=name,
            namespace="test",
            external_id=name,
            now=replacement if name.startswith("model-new") else before,
        )
    for name in ("model-old", "model-new", "agent-old", "agent-current"):
        contains = await seed_relationship(
            session,
            from_resource=resources["vm"],
            to_resource=resources[name],
            type_="contains",
            valid_from=resources[name].first_seen,
        )
        run = await seed_relationship(
            session,
            from_resource=resources[name],
            to_resource=resources[name + "-instance"],
            type_="runs",
            valid_from=resources[name].first_seen,
        )
        if name in {"model-old", "agent-old"}:
            contains.valid_to = run.valid_to = replacement
        await seed_relationship(
            session,
            from_resource=resources[name + "-instance"],
            to_resource=resources["model-service" if name.startswith("model") else "agent-service"],
            type_="instance_of",
            valid_from=resources[name].first_seen,
        )
    for source, target, start in (
        ("agent-old-instance", "model-old-instance", before),
        ("agent-current-instance", "model-old-instance", before),
        ("agent-current-instance", "model-new-instance", replacement),
    ):
        await seed_relationship(
            session,
            from_resource=resources[source],
            to_resource=resources[target],
            type_="depends_on",
            valid_from=start,
        )

    def successful(task_id, instance, started_at, duration):
        return TaskRun(
            tenant_id=TENANT,
            project_id=PROJECT,
            task_id=task_id,
            service_instance_id=resources[instance].id,
            started_at=started_at,
            ended_at=started_at + timedelta(milliseconds=duration),
            status="succeeded",
            status_provenance="observed",
            duration_ms=duration,
            model_duration_ms=duration,
        )

    for index in range(40):
        start = before + timedelta(minutes=2, seconds=index * 2)
        session.add(successful(f"retired-{index}", "agent-old-instance", start, 60000))
        session.add(successful(f"previous-model-{index}", "agent-current-instance", start, 60000))
    for index in range(current_samples):
        session.add(
            successful(
                f"current-{index}",
                "agent-current-instance",
                replacement + timedelta(seconds=10 + index * 5),
                1000,
            )
        )
    # A small, normal Agent quota can throttle without elevated model latency.
    # Its raw R2 incident must not make every causal task ineligible for bootstrap.
    normal_quota = Event(
        tenant_id=TENANT,
        project_id=PROJECT,
        event_id="normal-agent-quota",
        type="container.stats.sample",
        resource_id=resources["agent-current"].id,
        resource_ref={"namespace": "test", "external_id": "agent-current"},
        occurred_at=replacement + timedelta(seconds=1),
        observed_at=replacement + timedelta(seconds=1),
        severity="warning",
        payload={"cpu_quota": 50000, "throttled_periods_delta": 100},
        source="test",
        provenance="observed",
        resolution_state="resolved",
    )
    session.add(normal_quota)
    await session.flush()
    await process_event_alerts(session, [normal_quota])
    raw_incident = (await session.execute(select(Incident))).scalar_one()
    raw_diagnosis = await generate_revision(session, raw_incident)
    assert not any(candidate["rule_id"] == "R2" for candidate in raw_diagnosis.candidates)
    session.add(
        Event(
            tenant_id=TENANT,
            project_id=PROJECT,
            event_id="current-slow",
            type="task.succeeded",
            resource_id=resources["agent-current-instance"].id,
            resource_ref={"namespace": "test", "external_id": "agent-current-instance"},
            occurred_at=fault,
            observed_at=fault,
            severity="info",
            payload={
                "model_duration_ms": 4000,
                "duration_ms": 4000,
                "model_instance_id": resources["model-new-instance"].source_id,
            },
            source="test",
            provenance="observed",
            resolution_state="resolved",
        )
    )
    session.add(
        Event(
            tenant_id=TENANT,
            project_id=PROJECT,
            event_id="current-quota",
            type="container.stats.sample",
            resource_id=resources["model-new"].id,
            resource_ref={"namespace": "test", "external_id": "model-new"},
            occurred_at=fault,
            observed_at=fault,
            severity="warning",
            payload={"cpu_quota": 15000, "throttled_periods_delta": 100},
            source="test",
            provenance="observed",
            resolution_state="resolved",
        )
    )
    incident = Incident(
        tenant_id=TENANT,
        project_id=PROJECT,
        fingerprint="new-model-fault",
        state="open",
        severity="P3",
        first_seen=before if reopened else fault,
        last_seen=fault,
        alert_count=2 if reopened else 1,
        reopen_count=int(reopened),
    )
    session.add(incident)
    if reopened:
        session.add(
            Alert(
                tenant_id=TENANT,
                project_id=PROJECT,
                resource_id=resources["model-new"].id,
                fingerprint=incident.fingerprint,
                rule_id="R2",
                rule_version="1.0.0",
                state="resolved",
                severity="warning",
                starts_at=before,
                ends_at=before + timedelta(minutes=5),
                source="test",
                source_alert_id="previous-episode",
                provenance="observed",
            )
        )
    session.add(
        Alert(
            tenant_id=TENANT,
            project_id=PROJECT,
            resource_id=resources["model-new"].id,
            fingerprint=incident.fingerprint,
            rule_id="R2",
            rule_version="1.0.0",
            state="firing",
            severity="warning",
            starts_at=fault,
            source="test",
            source_alert_id="new-model",
            provenance="observed",
        )
    )
    await session.flush()
    counts_before = [
        await session.scalar(select(func.count()).select_from(table))
        for table in (Event, TaskRun, Relationship)
    ]
    nodes, _, _ = await effective_topology(
        session, PROJECT, fault, root_id=resources["model-new"].id
    )
    assert resources["agent-old-instance"].id not in {node.id for node in nodes}
    historical, _, _ = await effective_topology(
        session, PROJECT, before + timedelta(minutes=1), root_id=resources["model-old"].id
    )
    assert resources["agent-old-instance"].id in {node.id for node in historical}
    context = await build_context(session, incident)
    assert context.baseline["sample_count"] == current_samples
    assert context.baseline["state"] == ("ready" if current_samples == 30 else "insufficient")
    assert context.baseline["window_end"] == (fault - timedelta(microseconds=1)).isoformat()
    assert incident.first_seen == (before if reopened else fault)
    if current_samples == 30:
        assert context.baseline["p95_ms"] == 1000
    diagnosis = await generate_revision(session, incident, context=context)
    r2 = [c for c in diagnosis.candidates if c["rule_id"] == "R2"]
    assert bool(r2) == (current_samples == 30)
    if r2:
        assert r2[0]["support_level"] == "high"
    comparison_at = datetime.now(UTC)
    exclusions = await alert_exclusion_windows(
        session,
        project_id=PROJECT,
        now=comparison_at,
        resource_ids=[resources["model-new"].id, resources["agent-current"].id],
    )
    # Only the complete current-model R2 diagnosis qualifies; the Agent's bare
    # quota signal and the model's unconfirmed prior episode never do.
    assert exclusions == ([(fault, comparison_at)] if current_samples == 30 else [])
    other_target_exclusions = await alert_exclusion_windows(
        session,
        project_id=PROJECT,
        now=comparison_at,
        resource_ids=[resources["model-new"].id, resources["agent-current"].id],
        r2_resource_ids=[resources["agent-current"].id],
    )
    assert other_target_exclusions == []
    counts_after = [
        await session.scalar(select(func.count()).select_from(table))
        for table in (Event, TaskRun, Relationship)
    ]
    assert counts_after == counts_before
    get_settings.cache_clear()
