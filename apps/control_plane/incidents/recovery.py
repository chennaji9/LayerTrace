"""恢复门禁（T101）：全部告警 resolved 且持续 60 秒才可恢复；
stale/unavailable 的采集状态不得视为恢复。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.models import Alert, Incident, SourceStatus


def can_recover(
    alerts: list[dict],
    *,
    stable_since: float | None,
    now: float | None,
    source_states: list[str] | None = None,
) -> bool:
    """恢复门禁（纯函数，便于单测）。

    - 全部归属告警 resolved；
    - 最后一个 resolved 起稳定超过 60 秒；
    - 关键采集源不处于 stale/unavailable。
    """
    if any(a.get("state") != "resolved" for a in alerts):
        return False
    if source_states and any(
        s in ("stale", "unavailable", "permission_denied") for s in source_states
    ):
        return False
    settings = get_settings()
    stable_window = settings.incident_recovery_stable_seconds
    if stable_since is None or now is None:
        return False
    return (now - stable_since) >= stable_window


async def evaluate_recovery(session: AsyncSession, incident: Incident, *, now: datetime) -> bool:
    alerts = (
        (
            await session.execute(
                select(Alert).where(
                    Alert.fingerprint == incident.fingerprint,
                    Alert.project_id == incident.project_id,
                )
            )
        )
        .scalars()
        .all()
    )
    alert_dicts = [
        {"state": a.state, "ends_at": a.ends_at.timestamp() if a.ends_at else None} for a in alerts
    ]

    # Only collectors of this incident's VM can block its recovery.
    from apps.control_plane.models import Resource

    resource_ids = [a.resource_id for a in alerts if a.resource_id]
    resources = (
        (await session.execute(select(Resource).where(Resource.id.in_(resource_ids))))
        .scalars()
        .all()
    )
    vm_ids = {
        str(r.id) if r.kind == "vm" else str((r.attributes or {}).get("vm_id", ""))
        for r in resources
    }
    sources = (
        (await session.execute(select(SourceStatus).where(SourceStatus.source_type == "probe")))
        .scalars()
        .all()
    )
    source_states = [
        source.state for source in sources if str(source.capabilities.get("vm_id", "")) in vm_ids
    ]

    latest_resolution = max((a.ends_at.timestamp() for a in alerts if a.ends_at), default=None)
    recoverable = can_recover(
        alert_dicts,
        stable_since=latest_resolution,
        now=now.timestamp(),
        source_states=source_states,
    )
    if recoverable and incident.state in ("open", "acknowledged"):
        incident.state = "resolved"
        incident.resolved_at = now
        await session.flush()
    return recoverable


async def sweep_recoveries(session: AsyncSession) -> int:
    """worker 周期任务：评估候选恢复。"""
    from datetime import UTC, datetime

    incidents = (
        (
            await session.execute(
                select(Incident).where(Incident.state.in_(["open", "acknowledged"]))
            )
        )
        .scalars()
        .all()
    )
    recovered = 0
    now = datetime.now(UTC)
    for incident in incidents:
        alerts = (
            (
                await session.execute(
                    select(Alert.state).where(
                        Alert.fingerprint == incident.fingerprint,
                        Alert.project_id == incident.project_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        if alerts and all(state == "resolved" for state in alerts):
            if await evaluate_recovery(session, incident, now=now):
                recovered += 1
    return recovered
