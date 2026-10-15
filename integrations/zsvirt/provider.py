"""Read-only ZSvirt REST inventory and native alarm adapter."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from apps.control_plane.integrations.protocols import (
    CapabilityReport,
    DiscoveredRelationship,
    DiscoveredResource,
    DiscoveryBatch,
    EventBatchOut,
    MetricEvidenceBatch,
    SourceHealth,
    StandardEvent,
)


def _platform_created_at(value: object) -> str | None:
    """ZSvirt local wall time is Asia/Shanghai; retain authoritative lifecycle time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.strptime(value, "%b %d, %Y %I:%M:%S %p")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        return parsed.astimezone(UTC).isoformat()
    except ValueError:
        return None


class ZSvirtAuthError(RuntimeError):
    """Expose only a fixed failure category, never credentials or native response text."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(
            "ZSvirt session expired" if code == "session_expired" else "ZSvirt authorization failed"
        )


_SESSION_CACHE: dict[tuple[str, str, str, str], tuple[str, str | None]] = {}


def _auth_error(body: dict) -> str | None:
    error = body.get("error")
    if not isinstance(error, dict):
        return None
    if error.get("code") == "ID.1001":
        return "session_expired"
    if error.get("code") == "SYS.1019":
        return "authorization_failed"
    return None


class ZSvirtProvider:
    def __init__(
        self,
        *,
        base_url: str | None,
        token: str | None,
        ca_path: str | None = None,
        provider_instance: str = "zsvirt-lab",
        account_name: str | None = None,
        password_hash: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        if self._base_url and not self._base_url.endswith("/zstack/v1"):
            self._base_url += "/zstack/v1"
        self._token, self._ca_path, self._transport = token, ca_path, transport
        self._account_name, self._password_hash = account_name, password_hash
        self._login_enabled = bool(account_name and password_hash)
        self._session_expires_at: str | None = None
        self._cache_key = (
            self._base_url,
            provider_instance,
            account_name or "",
            hashlib.sha256(((password_hash or "") + "|" + (token or "")).encode()).hexdigest(),
        )
        if self._login_enabled and self._cache_key in _SESSION_CACHE:
            self._token, self._session_expires_at = _SESSION_CACHE[self._cache_key]
        self.instance = provider_instance
        self.enabled = bool(base_url and (token or self._login_enabled))
        self._health = SourceHealth(state="degraded" if self.enabled else "not_configured")
        self._event_endpoints: set[str] = set()
        self._missing: set[str] = set()

    def capabilities(self) -> CapabilityReport:
        if not self.enabled:
            return CapabilityReport(notes={"state": "not_configured"})
        return CapabilityReport(
            resource_kinds=["host", "vm"],
            fields={
                "host": ["uuid", "name", "managementIp", "clusterUuid", "state", "status"],
                "vm": ["uuid", "name", "hostUuid", "state", "cpuNum", "memorySize", "vmNics"],
            },
            events=sorted(self._event_endpoints),
            metrics=[],
            gpu_mapping=False,
            pagination="offset",
            missing_permissions=sorted(self._missing),
            notes={
                "state": self._health.state,
                "adapter": "zsvirt-rest-1.0",
                "provider_instance": self.instance,
                "authentication": "account_login" if self._login_enabled else "token_only",
                "session_expires_at": self._session_expires_at,
                "metrics": "Native alarm values only; utilization and GPU mapping unavailable",
            },
        )

    async def _login(self, client: httpx.AsyncClient) -> None:
        response = await client.put(
            self._base_url + "/accounts/login",
            json={
                "logInByAccount": {
                    "accountName": self._account_name,
                    "password": self._password_hash,
                }
            },
            headers={"Authorization": ""},
        )
        if response.status_code >= 400:
            raise ZSvirtAuthError("authorization_failed")
        try:
            body = response.json()
            inventory = body.get("inventory") or {}
            token = inventory.get("uuid")
            if "error" in body or not isinstance(token, str) or not token:
                raise ZSvirtAuthError("authorization_failed")
        except (ValueError, TypeError, AttributeError) as exc:
            raise ZSvirtAuthError("authorization_failed") from exc
        self._token = token
        self._session_expires_at = _platform_created_at(inventory.get("expiredDate"))
        _SESSION_CACHE[self._cache_key] = (token, self._session_expires_at)

    async def _inventory_page(self, client: httpx.AsyncClient, path: str, params) -> dict:
        for attempt in range(2):
            response = await client.get(
                self._base_url + path,
                params=params,
                headers={"Authorization": f"OAuth {self._token}"},
            )
            try:
                body = response.json()
            except ValueError:
                body = {}
            auth_error = _auth_error(body)
            expired = response.status_code == 401 or auth_error == "session_expired"
            if expired and self._login_enabled and attempt == 0:
                await self._login(client)
                continue
            if expired:
                raise ZSvirtAuthError("session_expired")
            if auth_error:
                raise ZSvirtAuthError(auth_error)
            response.raise_for_status()
            if "error" in body or not isinstance(body.get("inventories"), list):
                raise ValueError("Invalid ZSvirt inventory response")
            return body
        raise ZSvirtAuthError("session_expired")

    async def _get_all(self, path: str, *, query: list[str] | None = None) -> list[dict]:
        parsed = urlparse(self._base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Invalid ZSvirt URL scheme")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ZSvirt HTTP requires a local SSH tunnel")
        records: list[dict] = []
        async with httpx.AsyncClient(
            timeout=15,
            verify=self._ca_path or True,
            transport=self._transport,
        ) as client:
            expires_at = (
                datetime.fromisoformat(self._session_expires_at)
                if self._session_expires_at
                else None
            )
            if self._login_enabled and (
                not self._token
                or (
                    expires_at is not None
                    and expires_at <= datetime.now(UTC) + timedelta(seconds=60)
                )
            ):
                await self._login(client)
            for offset in range(0, 100_000, 500):
                params = [("start", str(offset)), ("limit", "500")]
                params.extend(("q", q) for q in query or [])
                body = await self._inventory_page(client, path, params)
                page = body["inventories"]
                records.extend(page)
                if len(page) < 500:
                    return records
        raise ValueError("ZSvirt pagination limit exceeded")

    def _record_failure(self, exc: Exception) -> None:
        denied = isinstance(exc, ZSvirtAuthError) or (
            isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}
        )
        code = (
            exc.code
            if isinstance(exc, ZSvirtAuthError)
            else "authorization_failed"
            if denied
            else "connection_failed"
        )
        self._health = SourceHealth(
            state="permission_denied" if denied else "unavailable",
            last_success_at=self._health.last_success_at,
            error_code=code,
            error_summary="ZSvirt session expired"
            if code == "session_expired"
            else "ZSvirt authorization failed"
            if denied
            else "ZSvirt read failed; inventory retained",
        )

    async def discover(self, cursor: str | None = None) -> DiscoveryBatch:
        if not self.enabled:
            return DiscoveryBatch()
        started, now = time.monotonic(), datetime.now(UTC)
        try:
            hosts = await self._get_all("/hosts")
            vms = await self._get_all("/vm-instances")
        except Exception as exc:
            self._record_failure(exc)
            raise
        batch = DiscoveryBatch(window_end=now)
        for kind, items in [("host", hosts), ("vm", vms)]:
            for item in items:
                rid = str(item["uuid"])
                attrs = {
                    k: item[k]
                    for k in [
                        "state",
                        "status",
                        "hostUuid",
                        "clusterUuid",
                        "zoneUuid",
                        "cpuNum",
                        "memorySize",
                    ]
                    if k in item
                }
                attrs.update(adapter_version="zsvirt-rest-1.0", platform_uuid=rid)
                created = _platform_created_at(item.get("createDate"))
                if created:
                    attrs["platform_created_at"] = created
                if kind == "host":
                    attrs["management_ip"] = item.get("managementIp")
                else:
                    attrs["ips"] = [n["ip"] for n in item.get("vmNics", []) if n.get("ip")]
                    attrs["limits"] = {
                        "vcpus": item.get("cpuNum"),
                        "memory_bytes": item.get("memorySize"),
                    }
                batch.resources.append(
                    DiscoveredResource(
                        kind=kind,
                        provider="zsvirt",
                        provider_instance=self.instance,
                        source_id=rid,
                        display_name=item.get("name"),
                        provenance="observed",
                        attributes=attrs,
                        namespace=f"zsvirt.{kind}",
                        external_id=rid,
                    )
                )
                if kind == "vm" and item.get("hostUuid"):
                    batch.relationships.append(
                        DiscoveredRelationship(
                            from_source_id=str(item["hostUuid"]),
                            type="hosts",
                            to_source_id=rid,
                            valid_from=now,
                            valid_to=None,
                            source="zsvirt",
                            provenance="observed",
                        )
                    )
        self._health = SourceHealth(
            state="healthy",
            last_success_at=now,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
        return batch

    async def read_events(self, cursor: str | None = None) -> EventBatchOut:
        if not self.enabled:
            return EventBatchOut()
        now = datetime.now(UTC)
        since = (
            int(cursor) - 300_000 if cursor else int((now - timedelta(days=1)).timestamp() * 1000)
        )
        events, latest, success = [], since, False
        for stream, endpoint in [
            ("alarm", "/zwatch/alarm-records"),
            ("event", "/zwatch/event-records"),
        ]:
            try:
                records = await self._get_all(endpoint, query=[f"createTime>={since}"])
                self._missing.discard(endpoint)
                self._event_endpoints.add(endpoint)
                success = True
            except Exception as exc:
                self._missing.add(endpoint)
                self._record_failure(exc)
                continue
            for row in records:
                timestamp = int(row.get("createTime", 0))
                resource = row.get("resourceUuid") or row.get("resourceId")
                if timestamp <= 0 or not resource:
                    continue
                latest = max(latest, timestamp)
                attrs = {
                    k: row[k]
                    for k in [
                        "alarmName",
                        "alarmStatus",
                        "alarmUuid",
                        "name",
                        "namespace",
                        "metricName",
                        "metricValue",
                        "threshold",
                        "period",
                        "comparisonOperator",
                        "emergencyLevel",
                    ]
                    if k in row
                }
                attrs.update(
                    platform_event_id=str(row.get("dataUuid") or row.get("id")),
                    platform_resource_uuid=str(resource),
                    platform_record_type=stream,
                )
                if row.get("alarmUuid"):
                    attrs["source_alarm_uuid"] = str(row["alarmUuid"])
                event_type = "platform.event"
                if stream == "alarm":
                    event_type = (
                        "platform.alarm_recovered"
                        if row.get("alarmStatus") in {"OK", "Normal"}
                        else "platform.alarm"
                    )
                native = str(row.get("namespace", ""))
                namespace = (
                    "zsvirt.host"
                    if native == "ZStack/Host"
                    else "zsvirt.vm"
                    if native == "ZStack/VM"
                    else "zsvirt.resource"
                )
                level = row.get("emergencyLevel")
                events.append(
                    StandardEvent(
                        event_id=f"{self.instance}:{stream}:{attrs['platform_event_id']}:{row.get('alarmStatus', '')}",
                        event_type=event_type,
                        namespace=namespace,
                        external_id=str(resource),
                        occurred_at=datetime.fromtimestamp(timestamp / 1000, UTC),
                        observed_at=now,
                        source=f"zsvirt/{self.instance}",
                        provenance="observed",
                        severity="critical"
                        if level == "Emergent"
                        else "error"
                        if level == "Important"
                        else "info",
                        attributes=attrs,
                        raw_ref=f"{endpoint}/{row.get('id')}",
                    )
                )
        if not success:
            raise RuntimeError("ZSvirt event endpoints unavailable; cursor retained")
        self._health.state = "degraded" if self._missing else "healthy"
        self._health.last_success_at = now
        self._health.missing_permissions = sorted(self._missing)
        self._health.error_summary = (
            "Some ZSvirt record endpoints are unavailable" if self._missing else None
        )
        return EventBatchOut(
            events=events,
            next_cursor=cursor if self._missing else str(max(latest, int(now.timestamp() * 1000))),
            window_start=datetime.fromtimestamp(max(since, 0) / 1000, UTC),
            window_end=now,
        )

    async def read_metrics(self, resource_ids, time_range) -> MetricEvidenceBatch:
        return MetricEvidenceBatch(capability_missing=["zsvirt_utilization_metrics", "gpu_mapping"])

    def health(self) -> SourceHealth:
        return self._health
