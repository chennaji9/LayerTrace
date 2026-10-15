"""Reconcile the Prometheus active alert set; absence after a successful poll resolves."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from apps.control_plane.config import get_settings
from apps.control_plane.incidents.service import fingerprint_for
from apps.control_plane.models import Alert, Incident, Resource

logger = logging.getLogger(__name__)


def _fingerprint_for_alert(project_id, labels):
    key = labels.get("container_id") or labels.get("container") or labels.get("vm_id") or "unknown"
    return fingerprint_for(
        project_id, labels.get("rule") or labels.get("alertname") or "prometheus", key
    ), key


async def poll_prometheus_alerts(session):
    settings = get_settings()
    if not settings.prometheus_url:
        return 0
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(f"{settings.prometheus_url}/api/v1/alerts")
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success" or not isinstance(
            payload.get("data", {}).get("alerts"), list
        ):
            raise ValueError("invalid Prometheus alert response")
    except Exception as error:
        logger.warning("Prometheus 告警读取失败: %s", type(error).__name__)
        return 0  # A failed poll can never mark alerts recovered.
    project_id = uuid.UUID(settings.project_id)
    tenant_id = uuid.UUID(settings.tenant_id)
    now = datetime.now(UTC)
    seen = set()
    written = 0
    for item in payload["data"]["alerts"]:
        labels = item.get("labels") or {}
        state = item.get("state")
        if state not in {"pending", "firing"}:
            continue
        active_at = item.get("activeAt")
        try:
            starts = datetime.fromisoformat(active_at.replace("Z", "+00:00"))
            if starts.tzinfo is None:
                continue
        except (AttributeError, ValueError):
            continue
        source_id = (
            "prom:"
            + hashlib.sha256(
                json.dumps({"labels": labels, "activeAt": active_at}, sort_keys=True).encode()
            ).hexdigest()
        )
        seen.add(source_id)
        alert = (
            await session.execute(
                select(Alert).where(
                    Alert.project_id == project_id,
                    Alert.source == "prometheus",
                    Alert.source_alert_id == source_id,
                )
            )
        ).scalar_one_or_none()
        resource_key = labels.get("container_id") or labels.get("container") or labels.get("vm_id")
        resource = (
            (
                await session.execute(
                    select(Resource).where(
                        Resource.project_id == project_id, Resource.source_id == resource_key
                    )
                )
            ).scalar_one_or_none()
            if resource_key
            else None
        )
        rule = labels.get("rule") or labels.get("alertname") or "prometheus"
        fingerprint = fingerprint_for(
            project_id, rule, resource.id if resource else resource_key or "unknown"
        )
        if alert is None:
            alert = Alert(
                tenant_id=tenant_id,
                project_id=project_id,
                rule_id=rule,
                rule_version="1.0.0",
                fingerprint=fingerprint,
                resource_id=resource.id if resource else None,
                state=state,
                severity=labels.get("severity", "warning"),
                starts_at=starts,
                source="prometheus",
                source_alert_id=source_id,
                provenance="observed",
                annotations=item.get("annotations") or {},
            )
            session.add(alert)
            written += 1
        else:
            if alert.state != state:
                alert.state = state
                alert.ends_at = None
                written += 1
        if state == "firing":
            incident = (
                await session.execute(
                    select(Incident).where(
                        Incident.project_id == project_id, Incident.fingerprint == fingerprint
                    )
                )
            ).scalar_one_or_none()
            severity = labels.get("severity", "P3").upper()
            severity = severity if severity in {"P1", "P2", "P3"} else "P3"
            if incident is None:
                session.add(
                    Incident(
                        tenant_id=tenant_id,
                        project_id=project_id,
                        fingerprint=fingerprint,
                        state="open",
                        severity=severity,
                        first_seen=starts,
                        last_seen=starts,
                        alert_count=1,
                        reopen_count=0,
                        title=labels.get("alertname", "Prometheus 告警"),
                    )
                )
            elif incident.state == "resolved":
                incident.state = "open"
                incident.reopen_count += 1
                incident.resolved_at = None
                incident.last_seen = max(incident.last_seen, starts)
                alert.annotations = {
                    **(alert.annotations or {}),
                    "incident_episode_started_at": starts.isoformat(),
                    "incident_reopen_count": incident.reopen_count,
                }
    active = (
        (
            await session.execute(
                select(Alert).where(
                    Alert.project_id == project_id,
                    Alert.source == "prometheus",
                    Alert.state.in_(["pending", "firing"]),
                )
            )
        )
        .scalars()
        .all()
    )
    for alert in active:
        if alert.source_alert_id not in seen:
            alert.state = "resolved"
            alert.ends_at = now
            written += 1
    await session.flush()
    return written
