"""Observed Docker events preserve occurrence time and stable source IDs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime


def docker_occurred_at(raw: dict) -> datetime:
    nanos = raw.get("time_nano")
    seconds = int(nanos) / 1e9 if nanos is not None else float(raw.get("time") or 0)
    if seconds <= 0:
        raise ValueError("Docker event has no occurrence timestamp")
    return datetime.fromtimestamp(seconds, UTC)


def docker_event_id(raw: dict) -> str:
    identity = {
        k: raw.get(k) for k in ("container_id", "action", "time_nano", "time", "actor_attributes")
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return f"docker-{digest}"


def container_event_payload(raw: dict) -> tuple[str, dict] | None:
    action = raw.get("action", "")
    attrs = {
        k: v
        for k, v in {
            "exit_code": raw.get("exit_code"),
            "image": raw.get("image"),
            "state": action,
            "docker_time_nano": raw.get("time_nano"),
        }.items()
        if v is not None
    }
    return {
        "oom": ("container.oom", {"oom_killed": True, **attrs}),
        "die": ("container.exit", attrs),
        "stop": ("container.state", {**attrs, "state": "stopped"}),
        "start": ("container.state", {**attrs, "state": "running"}),
        "restart": ("container.state", {**attrs, "state": "restarted"}),
        "destroy": ("container.state", {**attrs, "state": "removed"}),
    }.get(action)


def build_event(
    *,
    event_type: str,
    container_id: str,
    payload: dict,
    source: str,
    provenance: str = "observed",
    occurred_at: datetime | None = None,
    event_id: str | None = None,
) -> dict:
    return {
        "schema_version": "1.0",
        "event_id": event_id or f"probe-{uuid.uuid4().hex}",
        "event_type": event_type,
        "resource_ref": {"namespace": "docker.container", "external_id": container_id},
        "occurred_at": (occurred_at or datetime.now(UTC)).isoformat(),
        "observed_at": datetime.now(UTC).isoformat(),
        "severity": "error" if event_type in ("container.oom", "container.exit") else "info",
        "source": source,
        "provenance": provenance,
        "attributes": payload,
    }


def stats_event_payload(
    stats: dict, *, previous: dict | None = None, limits: dict | None = None
) -> tuple[str, dict]:
    cpu = stats.get("cpu_stats") or {}
    prev = (previous or {}).get("cpu_stats") or stats.get("precpu_stats") or {}
    cpu_delta = max(
        0,
        (cpu.get("cpu_usage") or {}).get("total_usage", 0)
        - (prev.get("cpu_usage") or {}).get("total_usage", 0),
    )
    system_delta = cpu.get("system_cpu_usage", 0) - prev.get("system_cpu_usage", 0)
    online_cpus = (
        cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    )
    percent = cpu_delta / system_delta * online_cpus * 100.0 if system_delta > 0 else 0.0
    memory = stats.get("memory_stats") or {}
    counters = memory.get("stats") or {}
    cache = counters.get(
        "total_inactive_file", counters.get("inactive_file", counters.get("cache", 0))
    )
    usage = int(memory.get("usage") or 0)
    throttle, old_throttle = cpu.get("throttling_data") or {}, prev.get("throttling_data") or {}
    periods, old_periods = int(throttle.get("periods") or 0), int(old_throttle.get("periods") or 0)
    throttled, old_throttled = (
        int(throttle.get("throttled_periods") or 0),
        int(old_throttle.get("throttled_periods") or 0),
    )
    pd, td = max(0, periods - old_periods), max(0, throttled - old_throttled)
    cpu_limit = float((limits or {}).get("cpu_limit_cores") or 0)
    cpu_period = int((limits or {}).get("cpu_period_us") or 100000)
    cpu_quota = int((limits or {}).get("cpu_quota") or (cpu_limit * cpu_period))
    return "container.stats.sample", {
        "cpu_percent": round(percent, 3),
        "memory_usage_bytes": usage,
        "memory_working_set_bytes": max(0, usage - min(usage, int(cache or 0))),
        "memory_limit_bytes": int(
            (limits or {}).get("memory_limit_bytes") or memory.get("limit") or 0
        ),
        "cpu_limit_cores": cpu_limit,
        "cpu_quota": cpu_quota,
        "cpu_period_us": cpu_period,
        "cpu_periods": periods,
        "throttled_periods": throttled,
        "throttled_periods_delta": td,
        "cpu_periods_delta": pd,
        "throttled_ratio": td / pd if pd else 0,
        "throttled_time_ns": int(throttle.get("throttled_time") or 0),
        "throttled_time_ns_delta": max(
            0,
            int(throttle.get("throttled_time") or 0) - int(old_throttle.get("throttled_time") or 0),
        ),
        "captured_at": stats.get("read") or datetime.now(UTC).isoformat(),
    }
