"""事件告警与最小事件单聚合（T046）。

- 为可告警事件类型（首版：container.oom）生成 Alert，按 (source, source_alert_id) 幂等；
- 按项目 + 规则 + 稳定故障对象指纹聚合并维护最小 Incident（计数、时间范围、重开）。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.diagnosis.evaluators.r0_process_exit import (
    OOM_EXIT_WINDOW_SECONDS,
    is_abnormal_exit,
)
from apps.control_plane.diagnosis.evaluators.r1_oom import _has_direct_oom
from apps.control_plane.models import Alert, Event, Incident

logger = logging.getLogger(__name__)

# 事件类型 -> (rule_id, 事件单严重度)。任务失败作为诊断证据，不单独建事件单。
ALERTABLE_EVENT_TYPES = {
    "container.exit": ("R0", "P2"),
    "container.oom": ("R1", "P2"),
    "vm.cpu.steal.sample": ("R4", "P3"),
    "gpu.cuda_oom": ("R4", "P2"),
    "container.stats.sample": ("R2", "P3"),
    "tool.failed": ("R3", "P2"),
    "tool.timed_out": ("R3", "P2"),
    "tool.error": ("R3", "P2"),
    "platform.alarm": ("R4", "P2"),
}
RULE_TITLES = {
    "R0": "容器进程异常退出",
    "R1": "容器内存不足（OOM）",
    "R2": "容器 CPU 配额瓶颈",
    "R3": "Agent 工具调用失败",
    "R4": "资源压力",
}


def _fingerprint(project_id, rule_id: str, resource_id) -> str:
    raw = f"{project_id}|{rule_id}|{resource_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def platform_alarm_identity(payload: dict) -> str | None:
    """Alarm UUID identifies the alarm, while data_uuid identifies one event record."""
    native = payload.get("native_record")
    for record in (payload, native if isinstance(native, dict) else {}):
        for key in ("source_alarm_uuid", "alarmUuid", "alarm_uuid"):
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


async def _exit_has_oom(session: AsyncSession, event: Event) -> bool:
    nearby = (
        (
            await session.execute(
                select(Event).where(
                    Event.project_id == event.project_id,
                    Event.resource_id == event.resource_id,
                    Event.type.in_({"container.oom", "container.cgroup_oom", "kernel.oom_kill"}),
                    Event.occurred_at.between(
                        event.occurred_at - timedelta(seconds=OOM_EXIT_WINDOW_SECONDS),
                        event.occurred_at + timedelta(seconds=OOM_EXIT_WINDOW_SECONDS),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return _has_direct_oom(
        [
            {"type": other.type, "resource_id": str(other.resource_id), "payload": other.payload}
            for other in nearby
        ],
        str(event.resource_id),
    )[0]


async def process_event_alerts(session: AsyncSession, events: list[Event] | None = None) -> int:
    """为可告警事件生成 Alert 并聚合 Incident。幂等。"""
    created = 0

    if events is None:
        events = (
            (
                await session.execute(
                    select(Event)
                    .where(Event.type.in_(ALERTABLE_EVENT_TYPES.keys()))
                    .order_by(Event.occurred_at.asc())
                )
            )
            .scalars()
            .all()
        )
    for event in events:
        if event.type not in ALERTABLE_EVENT_TYPES:
            continue
        if not event.resource_id:
            continue
        rule_id, incident_severity = ALERTABLE_EVENT_TYPES[event.type]
        if rule_id == "R0":
            if not is_abnormal_exit({"type": event.type, "payload": event.payload}):
                continue
            if await _exit_has_oom(session, event):
                continue
        elif rule_id == "R2":
            payload = event.payload or {}
            if (payload.get("cpu_quota") or 0) <= 0 or (
                payload.get("throttled_periods_delta") or 0
            ) <= 0:
                continue
            ratio = payload.get("throttled_ratio")
            if ratio is not None and float(ratio) < 0.2:
                continue
        elif event.type == "platform.alarm":
            if event.severity not in ("warning", "error", "critical"):
                continue
        elif event.type == "vm.cpu.steal.sample":
            if (event.payload or {}).get("steal_percent", 0) < 10:
                continue
        elif event.severity not in ("error", "critical"):
            continue
        source_alert_id = f"event:{event.id}"

        existing = (
            await session.execute(
                select(Alert).where(
                    Alert.source == "event", Alert.source_alert_id == source_alert_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        fingerprint = _fingerprint(event.project_id, rule_id, event.resource_id)
        # 规则版本取规则元数据当前版本（R1 首版 1.0.0）
        rule_version = "1.0.0"
        annotations = {"event_type": event.type}
        if event.type == "platform.alarm":
            alarm_uuid = platform_alarm_identity(event.payload or {})
            annotations.update(
                {
                    "source_alarm_uuid": alarm_uuid,
                    "recovery_identity_missing": alarm_uuid is None,
                }
            )
        alert = Alert(
            tenant_id=event.tenant_id,
            project_id=event.project_id,
            rule_id=rule_id,
            rule_version=rule_version,
            fingerprint=fingerprint,
            resource_id=event.resource_id,
            state="firing",
            severity=event.severity,
            starts_at=event.occurred_at,
            source="event",
            source_alert_id=source_alert_id,
            provenance=event.provenance,
            annotations=annotations,
        )
        session.add(alert)
        created += 1

        incident = (
            await session.execute(
                select(Incident).where(
                    Incident.project_id == event.project_id,
                    Incident.fingerprint == fingerprint,
                )
            )
        ).scalar_one_or_none()
        if incident is None:
            session.add(
                Incident(
                    tenant_id=event.tenant_id,
                    project_id=event.project_id,
                    fingerprint=fingerprint,
                    state="open",
                    severity=incident_severity,
                    first_seen=event.occurred_at,
                    last_seen=event.occurred_at,
                    alert_count=1,
                    reopen_count=0,
                    title=(
                        "ZSvirt 平台告警"
                        if event.type == "platform.alarm"
                        else f"{rule_id} {RULE_TITLES[rule_id]}"
                    ),
                )
            )
        else:
            incident.alert_count = (
                await session.execute(
                    select(func.count(Alert.id)).where(
                        Alert.project_id == event.project_id,
                        Alert.fingerprint == fingerprint,
                    )
                )
            ).scalar_one() or incident.alert_count
            incident.last_seen = max(incident.last_seen, event.occurred_at)
            if incident.state == "resolved":
                # 同一指纹再次触发：重开并保留历史
                incident.state = "open"
                incident.reopen_count += 1
                incident.resolved_at = None
                alert.annotations = {
                    **alert.annotations,
                    "incident_episode_started_at": event.occurred_at.isoformat(),
                    "incident_reopen_count": incident.reopen_count,
                }

    if created:
        await session.flush()
        logger.info("事件告警处理完成，新增 Alert %d", created)
    return created


async def process_event_recoveries(session: AsyncSession, events: list[Event]) -> int:
    """Resolve raw alerts only from a fresh, explicit observation of the same object."""
    resolved = 0
    for event in events:
        if event.resource_id is None:
            continue
        rules = []
        if event.type in {"container.start", "container.started", "container.recovered"} or (
            event.type == "container.state"
            and (event.payload or {}).get("state") in {"running", "restarted"}
        ):
            rules.extend(("R0", "R1"))
        if event.type == "container.stats.sample" and "throttled_periods_delta" in (
            event.payload or {}
        ):
            if (event.payload.get("throttled_periods_delta") or 0) == 0 and (
                event.payload.get("cpu_percent") or 0
            ) < 95:
                rules.append("R2")
        if event.type in {"tool.succeeded", "tool.completed", "tool.recovered"}:
            rules.append("R3")
        if event.type == "platform.alarm_recovered":
            if platform_alarm_identity(event.payload or {}) is None:
                logger.info("平台恢复事件 %s 缺少稳定告警 ID，保留现有告警状态", event.id)
                continue
            rules.append("R4")
        if not rules:
            continue
        alerts = (
            (
                await session.execute(
                    select(Alert).where(
                        Alert.project_id == event.project_id,
                        Alert.resource_id == event.resource_id,
                        Alert.rule_id.in_(rules),
                        Alert.source == "event",
                        Alert.state == "firing",
                        Alert.starts_at < event.occurred_at,
                    )
                )
            )
            .scalars()
            .all()
        )
        for alert in alerts:
            if event.type == "platform.alarm_recovered" and (
                (alert.annotations or {}).get("event_type") != "platform.alarm"
                or (alert.annotations or {}).get("source_alarm_uuid")
                != platform_alarm_identity(event.payload or {})
            ):
                continue
            alert.state = "resolved"
            alert.ends_at = event.occurred_at
            alert.annotations = {**(alert.annotations or {}), "recovery_event_id": str(event.id)}
            resolved += 1
    if resolved:
        await session.flush()
    return resolved
