"""Container reconciliation, dependency identity and restart-safe event cursor."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from agents.vm_probe.docker_client import DockerProxyClient
from agents.vm_probe.runtime import container_events_from_docker, convert_container


def config_hash(inspect: dict) -> str:
    host = inspect.get("HostConfig") or {}
    relevant = {
        key: host.get(key) for key in ("Memory", "MemorySwap", "CpuQuota", "CpuPeriod", "NanoCpus")
    }
    relevant["Image"] = inspect.get("Image")
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def discover(client: DockerProxyClient, vm_external_id: str) -> list:
    return [
        convert_container(client.inspect_container(summary["Id"]), vm_external_id=vm_external_id)
        for summary in client.list_containers()
    ]


def inventory_payload(instances: list, *, metrics: dict | None = None) -> dict:
    now = datetime.now(UTC).isoformat()
    by_name = {
        inst.attributes.get("service_name"): inst
        for inst in instances
        if inst.attributes.get("state") == "running"
    }
    dependencies = []
    containers = []
    for inst in instances:
        attrs = inst.attributes
        containers.append(
            {
                "container_id": inst.source_id,
                "name": inst.display_name,
                "service_name": attrs.get("service_name"),
                "instance_id": attrs["service_instance"],
                "state": attrs["state"],
                "started_at": attrs["started_at"],
                "created_at": attrs["created_at"],
                "limits": inst.limits,
                "image_digest": inst.image_digest,
                "labels": attrs["labels"],
                "metrics": (metrics or {}).get(inst.source_id, {}),
            }
        )
        for name in filter(None, (n.strip() for n in attrs.get("depends_on", "").split(","))):
            target = by_name.get(name)
            if target:
                dependencies.append(
                    {
                        "from_instance_id": attrs["service_instance"],
                        "to_instance_id": target.attributes["service_instance"],
                    }
                )
    return {
        "observed_at": now,
        "complete": True,
        "containers": containers,
        "dependencies": dependencies,
    }


def write_identity(path: str, instances: list) -> None:
    services = {}
    for inst in instances:
        name = inst.attributes.get("service_name")
        if name:
            services[name] = {
                "container_id": inst.source_id,
                "instance_id": inst.attributes["service_instance"],
                "started_at": inst.attributes["started_at"],
                "state": inst.attributes["state"],
            }
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"observed_at": datetime.now(UTC).isoformat(), "services": services}),
        encoding="utf-8",
    )
    temporary.chmod(0o644)
    temporary.replace(target)


class EventBackfill:
    """Overlap one second; stable event IDs make retransmission idempotent."""

    def __init__(self, spool=None) -> None:
        self.spool = spool
        self.since = spool.get_meta("docker_since") if spool else None
        self._pending_until: int | None = None
        self.gaps: list[dict] = []

    def poll(self, client: DockerProxyClient) -> list[dict]:
        until = int(time.time()) - 1
        since = int(self.since) - 1 if self.since is not None else until - 30
        events = container_events_from_docker(
            client.container_events(since=max(0, since), until=until)
        )
        self._pending_until = until
        return events

    def commit_cursor(self) -> None:
        if self._pending_until is None:
            raise RuntimeError("Cannot commit a Docker cursor without a completed poll")
        self.since = self._pending_until
        if self.spool:
            self.spool.set_meta("docker_since", self.since)
