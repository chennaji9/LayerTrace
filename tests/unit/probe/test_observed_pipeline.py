"""Probe/workload protocol checks without a database or external services."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from agents.vm_probe.__main__ import flush_outbox
from agents.vm_probe.docker_client import DockerProxyClient
from agents.vm_probe.events import docker_event_id, docker_occurred_at, stats_event_payload
from agents.vm_probe.identity import ProbeIdentity
from agents.vm_probe.runtime import container_events_from_docker, convert_container
from agents.vm_probe.spool import BoundedSpool
from scenarios.real_lab import inspect

CID = "a" * 64


def test_ndjson_preserves_raw_time_and_stable_identity():
    events = [
        {
            "Type": "container",
            "Action": "die",
            "Actor": {"ID": CID, "Attributes": {"exitCode": "137"}},
            "time": 1700000000,
            "timeNano": 1700000000123456789,
        },
        {
            "Type": "container",
            "Action": "start",
            "Actor": {"ID": CID, "Attributes": {}},
            "time": 1700000001,
            "timeNano": 1700000001234567890,
        },
    ]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, text="\n".join(json.dumps(value) for value in events) + "\n")

    client = DockerProxyClient("http://docker")
    client._client.close()
    client._client = httpx.Client(base_url="http://docker", transport=httpx.MockTransport(handler))
    raw = client.container_events(since=1700000000, until=1700000010)
    assert len(raw) == 2
    assert requests[0].url.params["until"] == "1700000010"
    mapped = container_events_from_docker(raw)
    assert mapped[0]["time_nano"] == 1700000000123456789
    assert docker_occurred_at(mapped[0]).timestamp() == pytest.approx(1700000000.123457, abs=1e-6)
    assert docker_event_id(mapped[0]) == docker_event_id(dict(mapped[0]))
    assert docker_event_id(mapped[0]) != docker_event_id(mapped[1])


def test_backfill_cursor_does_not_skip_time_spent_fetching(monkeypatch):
    from agents.vm_probe.discovery import EventBackfill

    monkeypatch.setattr("agents.vm_probe.discovery.time.time", lambda: 101)
    client = SimpleNamespace(container_events=lambda **kwargs: [])
    backfill = EventBackfill()
    assert backfill.poll(client) == []
    monkeypatch.setattr("agents.vm_probe.discovery.time.time", lambda: 150)
    backfill.commit_cursor()
    assert backfill.since == 100


def test_start_time_distinguishes_restart_and_rebuild():
    def value(cid, started):
        return {
            "Id": cid,
            "Name": "/same-name",
            "State": {"Status": "running", "StartedAt": started},
            "HostConfig": {"NanoCpus": 2000000000},
            "Config": {"Labels": {"layertrace.service": "demo-agent"}},
        }

    first = convert_container(value(CID, "2026-10-14T00:00:00.123456789Z"), vm_external_id="vm")
    restarted = convert_container(value(CID, "2026-10-14T00:01:00.123456789Z"), vm_external_id="vm")
    rebuilt = convert_container(
        value("b" * 64, "2026-10-14T00:00:00.123456789Z"), vm_external_id="vm"
    )
    assert len({item.attributes["service_instance"] for item in (first, restarted, rebuilt)}) == 3
    assert first.attributes["service_instance"] == CID + "@2026-10-14T00:00:00.123456789Z"
    assert first.limits["cpu_limit_cores"] == 2


def test_outbox_survives_restart_and_failed_ack(tmp_path):
    path = tmp_path / "outbox.sqlite"
    spool = BoundedSpool(max_bytes=10000, max_seconds=600, path=path)
    spool.enqueue({"event_id": "stable-event", "blob": "x"})
    assert spool.peek_batch() == spool.peek_batch()
    assert spool.depth == 1
    spool.close()
    spool = BoundedSpool(max_bytes=10000, max_seconds=600, path=path)
    identity = SimpleNamespace(
        probe_id="probe", auth_headers=lambda: {"Authorization": "Bearer secret"}
    )
    with httpx.Client(
        base_url="http://api", transport=httpx.MockTransport(lambda request: httpx.Response(503))
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            flush_outbox(client, identity, spool)
    assert spool.depth == 1
    # Unresolved is a subset of accepted, not an additional ACK count.
    with httpx.Client(
        base_url="http://api",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                202, json={"accepted": 1, "duplicate": 0, "unresolved": 1, "rejected": 0}
            )
        ),
    ) as client:
        assert flush_outbox(client, identity, spool) == 1
    assert spool.depth == 0


def test_outbox_rejects_partial_ack_without_loss(tmp_path):
    spool = BoundedSpool(max_bytes=10000, max_seconds=600, path=tmp_path / "q")
    spool.enqueue({"event_id": "e"})
    identity = SimpleNamespace(probe_id="p", auth_headers=lambda: {})
    with httpx.Client(
        base_url="http://api",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                202, json={"accepted": 0, "duplicate": 0, "unresolved": 0, "rejected": 1}
            )
        ),
    ) as client:
        with pytest.raises(RuntimeError):
            flush_outbox(client, identity, spool)
    assert spool.depth == 1


def test_stats_working_set_and_throttling_delta():
    old = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 100},
            "system_cpu_usage": 1000,
            "throttling_data": {"periods": 10, "throttled_periods": 2, "throttled_time": 100},
        }
    }
    raw = {
        "cpu_stats": {
            "cpu_usage": {"total_usage": 300},
            "system_cpu_usage": 2000,
            "online_cpus": 4,
            "throttling_data": {"periods": 20, "throttled_periods": 7, "throttled_time": 500},
        },
        "memory_stats": {
            "usage": 1000,
            "stats": {"inactive_file": 200, "anon": 900, "file": 10000},
            "limit": 4000,
        },
    }
    _, payload = stats_event_payload(raw, previous=old, limits={"cpu_limit_cores": 0.1})
    assert payload["memory_working_set_bytes"] == 800
    assert payload["cpu_percent"] == 80
    assert payload["throttled_periods_delta"] == 5
    assert payload["throttled_ratio"] == 0.5
    raw["memory_stats"]["stats"]["inactive_file"] = 2000
    assert stats_event_payload(raw)[1]["memory_working_set_bytes"] == 0


def test_credential_renews_before_expiry(tmp_path):
    config = SimpleNamespace(credential_path=str(tmp_path / "identity"))
    identity = ProbeIdentity(config)
    identity.expires_at = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    assert not identity.expired()
    assert identity.expired(margin_seconds=60)


def test_same_container_restart_waits_for_fresh_identity(tmp_path, monkeypatch):
    from workloads.demo_agent import events

    process_start = datetime.now(UTC)
    path = tmp_path / "services.json"
    document = {
        "observed_at": (process_start - timedelta(seconds=5)).isoformat(),
        "services": {
            "demo-agent": {"container_id": CID, "instance_id": CID + "@old", "state": "running"}
        },
    }
    path.write_text(json.dumps(document))
    monkeypatch.setenv("LAYERTRACE_IDENTITY_FILE", str(path))
    monkeypatch.setenv("LAYERTRACE_SERVICE", "demo-agent")
    monkeypatch.setenv("HOSTNAME", CID[:12])
    monkeypatch.delenv("DEMO_SERVICE_INSTANCE", raising=False)
    monkeypatch.setattr(events, "_process_started_at", process_start)
    with pytest.raises(RuntimeError, match="process start"):
        events.service_identity(require_current=True)
    document["observed_at"] = (process_start + timedelta(seconds=1)).isoformat()
    document["services"]["demo-agent"]["instance_id"] = CID + "@new"
    path.write_text(json.dumps(document))
    assert events.service_identity(require_current=True)["instance_id"] == CID + "@new"


def test_host_network_reads_container_mount_identity_not_vm_hostname():
    from workloads.demo_agent.events import parse_local_container_id

    mount = (
        f"40 30 8:1 /var/lib/docker/containers/{CID}/hostname /etc/hostname rw - ext4 /dev/sda1 rw"
    )
    assert parse_local_container_id(mount, "0::/", "layertrace-business") == CID
    assert (
        parse_local_container_id("", f"0::/system.slice/docker-{CID}.scope", "layertrace-business")
        == CID
    )
    assert parse_local_container_id("", "0::/", "layertrace-business") is None
    assert parse_local_container_id("", "0::/", CID[:12]) == CID[:12]


def test_fault_cli_refuses_unlabelled_target(monkeypatch):
    monkeypatch.setattr(
        "scenarios.real_lab.docker", lambda *args: json.dumps([{"Config": {"Labels": {}}}])
    )
    with pytest.raises(PermissionError):
        inspect("any-production-container")
