"""Durable workload telemetry; content and tool argument text stay out of events."""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agents.vm_probe.spool import PRIORITY_HIGH, BoundedSpool

API_BASE = os.environ.get("LAYERTRACE_API", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("LAYERTRACE_TOKEN", "")
logger = logging.getLogger("layertrace.workload.events")
_process_started_at = datetime.now(UTC)
_emitter = None
_emitter_lock = threading.Lock()


def parse_local_container_id(mount_info: str, cgroup: str, hostname: str) -> str | None:
    """Read Docker's own hostname mounts, then its cgroup, then a hex hostname."""
    candidates = set(
        re.findall(
            r"/docker/containers/([0-9a-f]{64})/(?:hostname|hosts|resolv\.conf)(?:\s|$)", mount_info
        )
    )
    candidates.update(re.findall(r"(?:/docker/|docker-)([0-9a-f]{64})(?:\.scope|/|\s|$)", cgroup))
    if len(candidates) == 1:
        return candidates.pop()
    if len(candidates) > 1:
        raise RuntimeError("Local Docker container identity is ambiguous")
    return hostname if re.fullmatch(r"(?:[0-9a-f]{12}|[0-9a-f]{64})", hostname) else None


def local_container_id() -> str | None:
    def read(path: str) -> str:
        try:
            return Path(path).read_text()
        except OSError:
            return ""

    return parse_local_container_id(
        read("/proc/self/mountinfo"), read("/proc/self/cgroup"), os.environ.get("HOSTNAME", "")
    )


def service_identity(service: str = "demo-agent", *, require_current: bool = False) -> dict:
    explicit = os.environ.get("DEMO_SERVICE_INSTANCE") if service == "demo-agent" else None
    if explicit:
        return {"instance_id": explicit, "container_id": os.environ.get("DEMO_CONTAINER_ID", "")}
    path = Path(os.environ.get("LAYERTRACE_IDENTITY_FILE", "/identity/services.json"))
    try:
        document = json.loads(path.read_text())
        value = document["services"][service]
        if require_current and service == os.environ.get("LAYERTRACE_SERVICE", "demo-agent"):
            local_id = local_container_id()
            if not local_id:
                raise RuntimeError(
                    "Local Docker identity is unverified; waiting for a verifiable container ID"
                )
            if not value["container_id"].startswith(local_id):
                raise RuntimeError("Probe identity belongs to a prior container")
            observed_at = datetime.fromisoformat(document["observed_at"].replace("Z", "+00:00"))
            if value.get("state") != "running" or observed_at < _process_started_at:
                raise RuntimeError("Waiting for an identity observed after this process start")
        return value
    except (OSError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Observed identity not yet available for {service}") from exc


def wait_for_identity(
    service: str = "demo-agent", *, timeout: float = 60, require_current: bool = False
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return service_identity(service, require_current=require_current)
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def build_task_event(
    event_type: str,
    task_id: str,
    attributes: dict,
    *,
    service: str = "demo-agent",
    instance_id: str | None = None,
) -> dict:
    instance_id = instance_id or service_identity(service)["instance_id"]
    now = datetime.now(UTC).isoformat()
    return {
        "schema_version": "1.0",
        "event_id": f"workload-{uuid.uuid4().hex}",
        "event_type": event_type,
        "resource_ref": {"namespace": "otel.service_instance", "external_id": instance_id},
        "occurred_at": now,
        "observed_at": now,
        "severity": "error"
        if event_type.endswith(("failed", "timed_out", "interrupted"))
        else "info",
        "source": os.environ.get("LAYERTRACE_OBSERVER", "demo-agent"),
        "provenance": "observed",
        "correlation": {"task_id": task_id},
        "attributes": attributes,
    }


class EventEmitter:
    def __init__(self):
        state_dir = Path(os.environ.get("WORKLOAD_STATE_DIR", "/var/lib/layertrace-workload"))
        self.spool = BoundedSpool(
            max_bytes=20 * 1024 * 1024, max_seconds=86400, path=state_dir / "outbox.sqlite3"
        )
        self.running = True
        self.wakeup = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def emit(self, payload: dict) -> bool:
        accepted = self.spool.enqueue(payload, priority=PRIORITY_HIGH)
        self.wakeup.set()
        return accepted

    def flush(self) -> int:
        with (
            self.lock,
            httpx.Client(verify=os.environ.get("LAYERTRACE_CA_FILE") or True, timeout=5) as client,
        ):
            batch = self.spool.peek_batch(max_items=100)
            if not batch:
                return 0
            response = client.post(
                f"{API_BASE}/api/v1/events:batch",
                json={"schema_version": "1.0", "events": batch},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            response.raise_for_status()
            ack = response.json()
            if (
                response.status_code != 202
                or ack.get("rejected")
                or ack.get("accepted", 0) + ack.get("duplicate", 0) != len(batch)
            ):
                raise RuntimeError("Incomplete event ACK")
            self.spool.acknowledge([item["event_id"] for item in batch])
            return len(batch)

    def _run(self):
        while self.running:
            try:
                self.flush()
            except Exception as exc:
                logger.warning("Telemetry buffered: %s", type(exc).__name__)
            self.wakeup.wait(2)
            self.wakeup.clear()

    def close(self):
        self.running = False
        self.wakeup.set()
        self.thread.join(timeout=6)
        if not self.thread.is_alive():
            self.spool.close()


def emitter() -> EventEmitter:
    global _emitter
    with _emitter_lock:
        if _emitter is None:
            _emitter = EventEmitter()
            atexit.register(_emitter.close)
    return _emitter


def emit_task_event(
    event_type: str,
    task_id: str,
    attributes: dict,
    *,
    service: str = "demo-agent",
    instance_id: str | None = None,
) -> bool:
    return emitter().emit(
        build_task_event(event_type, task_id, attributes, service=service, instance_id=instance_id)
    )
