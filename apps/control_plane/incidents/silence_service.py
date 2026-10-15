"""静默服务（T102）：只影响通知，不隐藏事实或状态。"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import Silence


def matches(
    silence: dict,
    *,
    fingerprint: str,
    rule_id: str | None = None,
    resource_id: str | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> bool:
    """静默命中判定（时间窗内 + matcher 匹配）。"""
    now = now or datetime.now(UTC)
    try:
        starts = datetime.fromisoformat(str(silence["starts_at"]).replace("Z", "+00:00"))
        ends = datetime.fromisoformat(str(silence["ends_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return False
    if not (starts <= now <= ends):
        return False
    matcher = silence.get("matcher") or {}
    if matcher.get("fingerprint"):
        return matcher["fingerprint"] == fingerprint
    if matcher.get("rule_id") and matcher["rule_id"] != rule_id:
        return False
    if matcher.get("resource_id") and matcher["resource_id"] != resource_id:
        return False
    if matcher.get("severity") and matcher["severity"] != severity:
        return False
    return bool(matcher)


async def create_silence(session: AsyncSession, *, request, tenant_id, project_id) -> Silence:
    silence = Silence(
        tenant_id=tenant_id,
        project_id=project_id,
        matcher=request.matcher.model_dump(mode="json", exclude_none=True),
        reason=request.reason,
        created_by=request.created_by,
        starts_at=request.starts_at,
        ends_at=request.ends_at,
    )
    session.add(silence)
    await session.flush()
    return silence


async def active_matches(
    session: AsyncSession,
    *,
    project_id,
    fingerprint: str,
    rule_id: str | None = None,
    resource_id: str | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> bool:
    """是否存在命中当前事件单的有效静默。"""
    return (
        await active_silence_until(
            session,
            project_id=project_id,
            fingerprint=fingerprint,
            rule_id=rule_id,
            resource_id=resource_id,
            severity=severity,
            now=now,
        )
        is not None
    )


async def active_silence_until(
    session: AsyncSession,
    *,
    project_id,
    fingerprint: str,
    rule_id: str | None = None,
    resource_id: str | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> datetime | None:
    """Return the latest end time among active silences matching an incident."""
    now = now or datetime.now(UTC)
    rows = (
        (
            await session.execute(
                select(Silence).where(Silence.project_id == project_id, Silence.ends_at > now)
            )
        )
        .scalars()
        .all()
    )
    payload = lambda s: {  # noqa: E731
        "matcher": s.matcher,
        "starts_at": s.starts_at.isoformat(),
        "ends_at": s.ends_at.isoformat(),
    }
    matching_ends = [
        s.ends_at
        for s in rows
        if matches(
            payload(s),
            fingerprint=fingerprint,
            rule_id=rule_id,
            resource_id=resource_id,
            severity=severity,
            now=now,
        )
    ]
    return max(matching_ends, default=None)


async def expire_silences(session: AsyncSession) -> int:
    """到期静默标记（时间窗判定已覆盖到期；此函数仅返回到期计数供状态展示）。"""
    now = datetime.now(UTC)
    rows = (await session.execute(select(Silence).where(Silence.ends_at <= now))).scalars().all()
    return len(rows)
