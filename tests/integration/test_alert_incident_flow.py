"""US5 集成测试：告警聚合、采集失联不恢复、60 秒稳定窗口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.models import Alert, Incident
from tests.seed_utils import seed_full_stack

pytestmark = [pytest.mark.integration]


async def _session(test_database_url: str):
    engine = create_async_engine(test_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_duplicate_alerts_single_incident(test_database_url, clean_db) -> None:
    from apps.control_plane.incidents.event_alerts import process_event_alerts
    from apps.control_plane.ingestion import repository as ingestion_repo
    from packages.contracts.schemas import EventBatch, EventInput

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    async with maker() as session:
        stack = await seed_full_stack(
            session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
        )
        refs = stack["refs"]

        # 同一故障对象的重复 OOM 事件（不同 event_id）
        base = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
        events = []
        for i in range(5):
            events.append(
                EventInput(
                    schema_version="1.0",
                    event_id=f"{tag}-oom-{i}",
                    event_type="container.oom",
                    resource_ref={
                        "namespace": "docker.container",
                        "external_id": refs["container"],
                    },
                    occurred_at=base + timedelta(seconds=i * 10),
                    observed_at=base + timedelta(seconds=i * 10),
                    severity="critical",
                    source="test",
                    provenance="observed",
                    attributes={"oom_killed": True},
                )
            )
        await ingestion_repo.ingest_batch(
            session,
            tenant_id=stack["vm"].tenant_id,
            project_id=stack["vm"].project_id,
            batch=EventBatch(schema_version="1.0", events=events),
        )
        await session.commit()

        # 重复处理告警两轮（幂等）
        await process_event_alerts(session)
        await session.commit()
        await process_event_alerts(session)
        await session.commit()

        incidents = (await session.execute(select(Incident))).scalars().all()
        alerts = (await session.execute(select(Alert))).scalars().all()
        assert len(incidents) == 1, f"重复告警只形成一个事件单，实际 {len(incidents)}"
        assert len(alerts) == 5, "每条事件事实保留为独立 Alert"
        assert incidents[0].alert_count >= 1
    await engine.dispose()


async def test_recovery_gate_stable_window(test_database_url, clean_db) -> None:
    from apps.control_plane.incidents.recovery import evaluate_recovery

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    now = datetime(2026, 9, 21, 12, 10, tzinfo=UTC)
    async with maker() as session:
        stack = await seed_full_stack(
            session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
        )
        incident = Incident(
            tenant_id=stack["vm"].tenant_id,
            project_id=stack["vm"].project_id,
            fingerprint=f"{tag}-fp",
            state="open",
            severity="P2",
            first_seen=now - timedelta(minutes=10),
            last_seen=now - timedelta(minutes=9),
            alert_count=2,
            reopen_count=0,
        )
        session.add(incident)
        await session.flush()

        # 场景 1：仍有 firing 告警 → 不恢复
        alerts = [
            Alert(
                tenant_id=incident.tenant_id,
                project_id=incident.project_id,
                rule_id="R1",
                rule_version="1.0.0",
                fingerprint=incident.fingerprint,
                resource_id=stack["container"].id,
                state="firing",
                severity="critical",
                starts_at=now - timedelta(minutes=10),
                source="event",
                source_alert_id=f"{tag}-a1",
                provenance="observed",
            ),
            Alert(
                tenant_id=incident.tenant_id,
                project_id=incident.project_id,
                rule_id="R1",
                rule_version="1.0.0",
                fingerprint=incident.fingerprint,
                resource_id=stack["container"].id,
                state="resolved",
                severity="critical",
                starts_at=now - timedelta(minutes=10),
                ends_at=now - timedelta(minutes=8),
                source="event",
                source_alert_id=f"{tag}-a2",
                provenance="observed",
            ),
        ]
        session.add_all(alerts)
        await session.commit()

        result = await evaluate_recovery(session, incident, now=now)
        assert result is False
        assert incident.state == "open"

        # 场景 2：全部 resolved 但只稳定 30 秒 → 不恢复
        for alert in alerts:
            alert.state = "resolved"
            alert.ends_at = now - timedelta(seconds=30)
        await session.commit()
        result = await evaluate_recovery(session, incident, now=now)
        assert result is False

        # 场景 3：全部 resolved 且稳定 60+ 秒 → 恢复
        for alert in alerts:
            alert.ends_at = now - timedelta(seconds=90)
        await session.commit()
        result = await evaluate_recovery(session, incident, now=now)
        assert result is True
        assert incident.state == "resolved"
        assert incident.resolved_at is not None
    await engine.dispose()


async def test_stale_source_blocks_recovery(test_database_url, clean_db) -> None:
    from apps.control_plane.incidents.recovery import evaluate_recovery
    from apps.control_plane.integrations.status_service import upsert_status

    engine, maker = await _session(test_database_url)
    tag = uuid.uuid4().hex[:6]
    now = datetime(2026, 9, 21, 12, 10, tzinfo=UTC)
    async with maker() as session:
        stack = await seed_full_stack(
            session, prefix=tag, now=datetime(2026, 9, 21, 0, 0, tzinfo=UTC)
        )
        incident = Incident(
            tenant_id=stack["vm"].tenant_id,
            project_id=stack["vm"].project_id,
            fingerprint=f"{tag}-fp2",
            state="open",
            severity="P2",
            first_seen=now - timedelta(minutes=10),
            last_seen=now - timedelta(minutes=5),
            alert_count=1,
            reopen_count=0,
        )
        session.add(incident)
        await session.flush()
        session.add(
            Alert(
                tenant_id=incident.tenant_id,
                project_id=incident.project_id,
                rule_id="R1",
                rule_version="1.0.0",
                fingerprint=incident.fingerprint,
                resource_id=stack["container"].id,
                state="resolved",
                severity="critical",
                starts_at=now - timedelta(minutes=10),
                ends_at=now - timedelta(seconds=120),
                source="event",
                source_alert_id=f"{tag}-b1",
                provenance="observed",
            )
        )
        # 采集源失联：stale
        await upsert_status(
            session, source_type="probe", source_instance=f"{tag}-probe", state="stale"
        )
        await session.commit()

        result = await evaluate_recovery(session, incident, now=now)
        assert result is False, "采集失联不得触发恢复"
        assert incident.state == "open"
    await engine.dispose()


async def test_reopen_records_episode_anchor_without_rewriting_first_seen(db_session, monkeypatch):
    from apps.control_plane.config import get_settings
    from apps.control_plane.diagnosis.context import build_context
    from apps.control_plane.incidents.event_alerts import (
        process_event_alerts,
        process_event_recoveries,
    )
    from apps.control_plane.incidents.recovery import evaluate_recovery
    from apps.control_plane.models import Event
    from tests.seed_utils import PROJECT, TENANT, seed_resource

    monkeypatch.setenv("INCIDENT_RECOVERY_STABLE_SECONDS", "60")
    get_settings.cache_clear()
    first = datetime.now(UTC) - timedelta(minutes=20)
    second = first + timedelta(minutes=10)
    resource = await seed_resource(
        db_session,
        kind="container",
        provider="test",
        source_id="reopen-container",
        namespace="test",
        external_id="reopen-container",
        now=first - timedelta(minutes=1),
    )

    def event(key, at, throttled):
        return Event(
            tenant_id=TENANT,
            project_id=PROJECT,
            event_id=key,
            resource_id=resource.id,
            resource_ref={"namespace": "test", "external_id": "reopen-container"},
            type="container.stats.sample",
            occurred_at=at,
            observed_at=at,
            severity="warning",
            payload={"cpu_quota": 15000, "throttled_periods_delta": throttled, "cpu_percent": 1},
            source="test",
            provenance="observed",
            resolution_state="resolved",
        )

    first_event = event("first-fault", first, 100)
    db_session.add(first_event)
    await db_session.flush()
    await process_event_alerts(db_session, [first_event])
    incident = (await db_session.execute(select(Incident))).scalar_one()
    recovery = event("first-recovered", first + timedelta(seconds=30), 0)
    db_session.add(recovery)
    await db_session.flush()
    await process_event_recoveries(db_session, [recovery])
    assert await evaluate_recovery(db_session, incident, now=first + timedelta(seconds=90))
    second_event = event("second-fault", second, 100)
    db_session.add(second_event)
    await db_session.flush()
    await process_event_alerts(db_session, [second_event])
    assert incident.reopen_count == 1
    assert incident.first_seen == first
    second_alert = (
        await db_session.execute(select(Alert).where(Alert.starts_at == second))
    ).scalar_one()
    assert second_alert.annotations["incident_reopen_count"] == 1
    assert second_alert.annotations["incident_episode_started_at"] == second.isoformat()
    # A collector gap within an unrecovered second episode must not move the anchor.
    second_alert.state = "resolved"
    second_alert.ends_at = second + timedelta(seconds=10)
    late_event = event("late-fault-observation", second + timedelta(minutes=5), 100)
    db_session.add(late_event)
    await db_session.flush()
    await process_event_alerts(db_session, [late_event])
    context = await build_context(db_session, incident)
    assert context.baseline["window_end"] == (second - timedelta(microseconds=1)).isoformat()
    assert incident.reopen_count == 1
    get_settings.cache_clear()
