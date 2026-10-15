"""通知输出端（T104）：可选 NotificationSink。

- 只消费控制面的事件单状态变化；
- 静默命中时不调用；
- 通知失败不得反向改变 Alert 或 Incident 事实。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.incidents.protocols_ext import IncidentTransition
from apps.control_plane.incidents.silence_service import active_matches
from apps.control_plane.models import Incident

logger = logging.getLogger(__name__)


class WebhookSink:
    """HTTP Webhook 输出（可选启用）。"""

    async def send(self, transition: IncidentTransition) -> bool:
        settings = get_settings()
        if not settings.notification_sink_url:
            return False
        try:
            res = await asyncio_post(
                settings.notification_sink_url,
                {
                    "incident_id": transition.incident_id,
                    "fingerprint": transition.fingerprint,
                    "from_state": transition.from_state,
                    "to_state": transition.to_state,
                    "severity": transition.severity,
                    "occurred_at": transition.occurred_at.isoformat(),
                },
            )
            return res
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响事实
            logger.warning("通知发送失败（不影响事件单事实）：%s", exc)
            return False


async def asyncio_post(url: str, payload: dict) -> bool:
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(url, json=payload)
        return 200 <= res.status_code < 300


async def notify_transition(session: AsyncSession, incident: Incident, from_state: str) -> None:
    """事件单状态变化 → 通知（静默命中跳过；失败只计数）。"""
    settings = get_settings()
    if not settings.notification_sink_enabled:
        return
    to_state = incident.state
    if to_state == from_state:
        return
    silenced = await active_matches(
        session,
        project_id=incident.project_id,
        fingerprint=incident.fingerprint,
        severity=incident.severity,
    )
    if silenced:
        # 静默只抑制通知；事实与状态已更新
        return
    sink = WebhookSink()
    delivered = await sink.send(
        IncidentTransition(
            incident_id=str(incident.id),
            fingerprint=incident.fingerprint,
            from_state=from_state,
            to_state=to_state,
            severity=incident.severity,
            occurred_at=datetime.now(UTC),
            silenced=False,
        )
    )
    if delivered:
        incident.notification_count = (incident.notification_count or 0) + 1
    await session.flush()
