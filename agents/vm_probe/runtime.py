"""Docker discovery types independent of the control-plane package."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SI_NAMESPACE = "otel.service_instance"
CONTAINER_NAMESPACE = "docker.container"


@dataclass
class RuntimeInstance:
    kind: str
    provider: str
    provider_instance: str
    source_id: str
    display_name: str | None
    provenance: str
    attributes: dict[str, Any] = field(default_factory=dict)
    restart_count: int = 0
    image_digest: str | None = None
    limits: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeDiscoveryBatch:
    instances: list[RuntimeInstance]
    vm_namespace: str
    vm_external_id: str


def service_instance_id(container_id: str, started_at: str) -> str:
    """Restart changes StartedAt; rebuild changes the full Docker ID."""
    return f"{container_id}@{started_at}"


def convert_container(
    inspect: dict, *, vm_external_id: str, provider_instance: str = "default"
) -> RuntimeInstance:
    host = inspect.get("HostConfig") or {}
    state = inspect.get("State") or {}
    labels = (inspect.get("Config") or {}).get("Labels") or {}
    started_at = state.get("StartedAt") or inspect.get("Created") or "never-started"
    container_id = inspect["Id"]
    quota, period = int(host.get("CpuQuota") or 0), int(host.get("CpuPeriod") or 100000)
    nano_cpus = int(host.get("NanoCpus") or 0)
    limits = {
        "memory_limit_bytes": int(host.get("Memory") or 0),
        "memory_swap_bytes": int(host.get("MemorySwap") or 0),
        "cpu_quota": quota,
        "cpu_period_us": period,
        "nano_cpus": nano_cpus,
        "cpu_limit_cores": nano_cpus / 1e9 if nano_cpus else (quota / period if quota > 0 else 0),
    }
    return RuntimeInstance(
        kind="container",
        provider="docker",
        provider_instance=provider_instance,
        source_id=container_id,
        display_name=(inspect.get("Name") or "").lstrip("/"),
        provenance="observed",
        restart_count=int(inspect.get("RestartCount") or 0),
        image_digest=inspect.get("Image"),
        limits=limits,
        attributes={
            "state": state.get("Status", "unknown"),
            "started_at": started_at,
            "created_at": inspect.get("Created") or started_at,
            "service_instance": service_instance_id(container_id, started_at),
            "service_name": labels.get("layertrace.service")
            or labels.get("com.docker.compose.service"),
            "vm_external_id": vm_external_id,
            "labels": {
                k: v
                for k, v in labels.items()
                if k.startswith(("layertrace.", "com.docker.compose."))
            },
            "depends_on": labels.get("layertrace.depends_on", ""),
        },
    )


def discovery_batch(instances: list[RuntimeInstance], vm_external_id: str) -> RuntimeDiscoveryBatch:
    return RuntimeDiscoveryBatch(instances, "zsvirt.vm", vm_external_id)


def container_events_from_docker(events: list[dict]) -> list[dict]:
    out = []
    for raw in events:
        if raw.get("Type", "container") != "container":
            continue
        actor = (raw.get("Actor") or {}).get("Attributes") or {}
        action = raw.get("Action") or raw.get("status", "")
        out.append(
            {
                "action": action,
                "container_id": raw.get("id") or (raw.get("Actor") or {}).get("ID"),
                "image": actor.get("image"),
                "name": actor.get("name"),
                "time": raw.get("time"),
                "time_nano": raw.get("timeNano"),
                "exit_code": actor.get("exitCode"),
                "oom": action == "oom",
                "actor_attributes": actor,
            }
        )
    return out
