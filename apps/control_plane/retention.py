"""Retention enforcement for PostgreSQL facts and evidence references."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import Settings, get_settings
from apps.control_plane.diagnosis.evidence import mark_expired_evidence
from apps.control_plane.models import Diagnosis, Event


@dataclass(frozen=True)
class RetentionResult:
    expired_evidence: int
    deleted_events: int
    deleted_diagnoses: int
    metrics_days: int
    span_days: int
    event_days: int
    diagnosis_days: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


async def enforce_retention(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> RetentionResult:
    """Apply configured PostgreSQL retention and preserve expired evidence summaries."""
    current = now or datetime.now(UTC)
    policy = settings or get_settings()
    expired_evidence = await mark_expired_evidence(session, now=current)

    event_result = await session.execute(
        delete(Event).where(
            Event.observed_at < current - timedelta(days=policy.retention_event_days)
        )
    )
    diagnosis_result = await session.execute(
        delete(Diagnosis).where(
            Diagnosis.created_at < current - timedelta(days=policy.retention_diagnosis_days)
        )
    )
    await session.flush()
    return RetentionResult(
        expired_evidence=expired_evidence,
        deleted_events=event_result.rowcount or 0,
        deleted_diagnoses=diagnosis_result.rowcount or 0,
        metrics_days=policy.retention_metrics_days,
        span_days=policy.retention_span_days,
        event_days=policy.retention_event_days,
        diagnosis_days=policy.retention_diagnosis_days,
    )
