"""Retention policy integration tests (T142)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.config import Settings
from apps.control_plane.models import Diagnosis, Event, Evidence, Incident
from apps.control_plane.retention import enforce_retention
from tests.seed_utils import PROJECT, TENANT

pytestmark = [pytest.mark.integration]


async def test_retention_deletes_old_events_and_marks_evidence_expired(
    test_database_url, clean_db
) -> None:
    now = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)
    old_id = uuid.uuid4()
    fresh_id = uuid.uuid4()
    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        incident = Incident(
            tenant_id=TENANT,
            project_id=PROJECT,
            fingerprint="retention-incident",
            state="resolved",
            severity="P2",
            first_seen=now - timedelta(days=40),
            last_seen=now - timedelta(days=29),
            resolved_at=now - timedelta(days=29),
            alert_count=1,
        )
        session.add(incident)
        await session.flush()
        session.add_all(
            [
                Event(
                    id=old_id,
                    event_id="retention-old",
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    resource_ref={"namespace": "test", "external_id": "old"},
                    type="test.event",
                    occurred_at=now - timedelta(days=31),
                    observed_at=now - timedelta(days=31),
                    severity="info",
                    payload={},
                    source="retention-test",
                    provenance="observed",
                    resolution_state="unresolved",
                ),
                Event(
                    id=fresh_id,
                    event_id="retention-fresh",
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    resource_ref={"namespace": "test", "external_id": "fresh"},
                    type="test.event",
                    occurred_at=now - timedelta(days=29),
                    observed_at=now - timedelta(days=29),
                    severity="info",
                    payload={},
                    source="retention-test",
                    provenance="observed",
                    resolution_state="unresolved",
                ),
                Evidence(
                    tenant_id=TENANT,
                    project_id=PROJECT,
                    kind="metric",
                    range_start=now - timedelta(days=8),
                    range_end=now - timedelta(days=8),
                    source_ref="metric:expired-retention-test",
                    summary={"average": 1.0},
                    query_parameters={},
                    provenance="observed",
                    expires_at=now,
                    source_expired=False,
                    retention_days=7,
                ),
                Diagnosis(
                    incident_id=incident.id,
                    revision=1,
                    window_start=now - timedelta(days=31, minutes=1),
                    window_end=now - timedelta(days=31),
                    candidates=[],
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=[],
                    missing_evidence=[],
                    observed_impact={},
                    potential_impact={},
                    rule_versions={},
                    time_confidence="trusted",
                    context_truncated=False,
                    created_at=now - timedelta(days=31),
                ),
                Diagnosis(
                    incident_id=incident.id,
                    revision=2,
                    window_start=now - timedelta(days=29, minutes=1),
                    window_end=now - timedelta(days=29),
                    candidates=[],
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=[],
                    missing_evidence=[],
                    observed_impact={},
                    potential_impact={},
                    rule_versions={},
                    time_confidence="trusted",
                    context_truncated=False,
                    created_at=now - timedelta(days=29),
                ),
            ]
        )
        await session.commit()

        result = await enforce_retention(
            session,
            now=now,
            settings=Settings(
                retention_metrics_days=7,
                retention_span_days=3,
                retention_event_days=30,
                retention_diagnosis_days=30,
            ),
        )
        await session.commit()

        remaining = (await session.execute(select(Event.event_id))).scalars().all()
        diagnosis_revisions = (
            await session.execute(select(Diagnosis.revision).order_by(Diagnosis.revision))
        ).scalars().all()
        evidence = (
            await session.execute(
                select(Evidence).where(Evidence.source_ref == "metric:expired-retention-test")
            )
        ).scalar_one()

    await engine.dispose()
    assert result.deleted_events == 1
    assert result.deleted_diagnoses == 1
    assert result.expired_evidence == 1
    assert remaining == ["retention-fresh"]
    assert diagnosis_revisions == [2]
    assert evidence.source_expired is True
    assert "source_expired_notice" in evidence.summary


def test_retention_defaults_and_deploy_file() -> None:
    settings = Settings()
    assert (settings.retention_metrics_days, settings.retention_span_days) == (7, 3)
    assert (settings.retention_event_days, settings.retention_diagnosis_days) == (30, 30)
    content = open("deploy/retention.env", encoding="utf-8").read()
    for entry in (
        "RETENTION_METRICS_DAYS=7",
        "RETENTION_SPAN_DAYS=3",
        "RETENTION_EVENT_DAYS=30",
        "RETENTION_DIAGNOSIS_DAYS=30",
    ):
        assert entry in content
