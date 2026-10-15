"""Real consumer/recovery boundaries: process exit, Docker start and alarm identity."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from apps.control_plane.incidents.event_alerts import process_event_alerts, process_event_recoveries
from apps.control_plane.models import Alert, Event, Incident, Resource

pytestmark = [pytest.mark.integration]


async def test_alarm_recovery_matches_alarm_uuid_and_docker_state(db_session):
    session = db_session
    project = uuid.UUID("00000000-0000-0000-0000-000000000001")
    now = datetime.now(UTC)
    resource = Resource(
        tenant_id=project,
        project_id=project,
        kind="vm",
        provider="zsvirt",
        provider_instance="default",
        source_id="recovery-vm",
        provenance="observed",
        first_seen=now,
        last_seen=now,
    )
    session.add(resource)
    await session.flush()

    def event(kind, offset, payload, target=None):
        target = target or resource
        at = now + timedelta(seconds=offset)
        return Event(
            event_id=uuid.uuid4().hex,
            tenant_id=project,
            project_id=project,
            resource_id=target.id,
            resource_ref={
                "namespace": "docker.container" if target.kind == "container" else "zsvirt.vm",
                "external_id": target.source_id,
            },
            type=kind,
            occurred_at=at,
            observed_at=at,
            severity="error",
            payload=payload,
            source="zsvirt",
            provenance="observed",
            resolution_state="resolved",
        )

    alarms = [
        event("platform.alarm", 0, {"source_alarm_uuid": "alarm-a", "data_uuid": "record-a"}),
        event("platform.alarm", 1, {"source_alarm_uuid": "alarm-b", "data_uuid": "record-b"}),
        event("vm.cpu.steal.sample", 2, {"steal_percent": 18}),
        event("platform.alarm", 3, {"data_uuid": "record-without-alarm-id"}),
    ]
    session.add_all(alarms)
    await session.flush()
    assert await process_event_alerts(session, alarms) == 4
    recovered = event(
        "platform.alarm_recovered",
        10,
        {"source_alarm_uuid": "alarm-a", "data_uuid": "new-record-a"},
    )
    session.add(recovered)
    await session.flush()
    assert await process_event_recoveries(session, [recovered]) == 1
    rows = (await session.execute(select(Alert).order_by(Alert.starts_at))).scalars().all()
    assert [row.state for row in rows] == ["resolved", "firing", "firing", "firing"]
    assert rows[-1].annotations["recovery_identity_missing"] is True
    unidentified = event("platform.alarm_recovered", 11, {"data_uuid": "record-without-alarm-id"})
    assert await process_event_recoveries(session, [unidentified]) == 0

    container = Resource(
        tenant_id=project,
        project_id=project,
        kind="container",
        provider="docker",
        provider_instance="test",
        source_id="b" * 64,
        provenance="observed",
        first_seen=now,
        last_seen=now,
    )
    session.add(container)
    await session.flush()
    exit137 = event("container.exit", 20, {"exit_code": 137, "signal": "SIGKILL"}, container)
    # An abnormal exit generates R0 even at warning severity.
    exit137.severity = "warning"
    session.add(exit137)
    await session.flush()
    assert await process_event_alerts(session, [exit137]) == 1
    incident = (
        await session.execute(select(Incident).where(Incident.title.like("R0%")))
    ).scalar_one()
    assert incident.title == "R0 容器进程异常退出"
    running = event("container.state", 30, {"state": "running"}, container)
    session.add(running)
    await session.flush()
    assert await process_event_recoveries(session, [running]) == 1
    process_alert = (await session.execute(select(Alert).where(Alert.rule_id == "R0"))).scalar_one()
    assert process_alert.state == "resolved"


async def test_direct_oom_exit_does_not_create_duplicate_process_incident(db_session):
    session = db_session
    project = uuid.UUID("00000000-0000-0000-0000-000000000001")
    now = datetime.now(UTC)
    resource = Resource(
        tenant_id=project,
        project_id=project,
        kind="container",
        provider="docker",
        provider_instance="test",
        source_id="a" * 64,
        provenance="observed",
        first_seen=now,
        last_seen=now,
    )
    session.add(resource)
    await session.flush()
    facts = []
    for kind, payload in [
        ("container.oom", {"oom_killed": True}),
        ("container.exit", {"exit_code": 137}),
    ]:
        facts.append(
            Event(
                event_id=uuid.uuid4().hex,
                tenant_id=project,
                project_id=project,
                resource_id=resource.id,
                resource_ref={"namespace": "docker.container", "external_id": "a" * 64},
                type=kind,
                occurred_at=now,
                observed_at=now,
                severity="error",
                payload=payload,
                source="vm-probe",
                provenance="observed",
                resolution_state="resolved",
            )
        )
    session.add_all(facts)
    await session.flush()
    assert await process_event_alerts(session, facts) == 1
    alerts = (await session.execute(select(Alert))).scalars().all()
    assert len(alerts) == 1 and alerts[0].rule_id == "R1"
    recovery = Event(
        event_id=uuid.uuid4().hex,
        tenant_id=project,
        project_id=project,
        resource_id=resource.id,
        resource_ref={"namespace": "docker.container", "external_id": "a" * 64},
        type="container.state",
        occurred_at=now + timedelta(seconds=20),
        observed_at=now + timedelta(seconds=20),
        severity="info",
        payload={"state": "restarted"},
        source="vm-probe",
        provenance="observed",
        resolution_state="resolved",
    )
    assert await process_event_recoveries(session, [recovery]) == 1
