"""Deployable probe: inventory, observed events, metrics and persistent delivery."""

from __future__ import annotations

import logging
import signal
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from agents.vm_probe.config import ProbeConfig
from agents.vm_probe.discovery import EventBackfill, discover, inventory_payload, write_identity
from agents.vm_probe.docker_client import DockerProxyClient
from agents.vm_probe.events import (
    build_event,
    container_event_payload,
    docker_event_id,
    docker_occurred_at,
    stats_event_payload,
)
from agents.vm_probe.identity import ProbeIdentity
from agents.vm_probe.metrics import MetricsExporter, samples_from_stats
from agents.vm_probe.spool import PRIORITY_HIGH, PRIORITY_LOW, BoundedSpool

logger = logging.getLogger("layertrace.probe")


def flush_outbox(api: httpx.Client, identity: ProbeIdentity, spool: BoundedSpool) -> int:
    sent = 0
    for _ in range(10):
        batch = spool.peek_batch(max_items=200)
        if not batch:
            break
        response = api.post(
            f"/api/v1/probes/{identity.probe_id}/events",
            json={"schema_version": "1.0", "events": batch},
            headers=identity.auth_headers(),
        )
        response.raise_for_status()
        result = response.json()
        stored = int(result.get("accepted", 0)) + int(result.get("duplicate", 0))
        if response.status_code != 202 or result.get("rejected") or stored != len(batch):
            raise RuntimeError("Incomplete ingestion acknowledgement; outbox retained")
        spool.acknowledge([event["event_id"] for event in batch])
        sent += len(batch)
    return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = ProbeConfig()
    errors = config.validate()
    if errors:
        raise SystemExit("; ".join(errors))
    running = True

    def stop(*_args):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    identity = ProbeIdentity(config)
    identity.load()
    spool = BoundedSpool(
        max_bytes=config.spool_max_bytes,
        max_seconds=config.spool_max_seconds,
        path=Path(config.state_dir) / "outbox.sqlite3",
    )
    client = DockerProxyClient(config.docker_proxy_url, container_label=config.container_label)
    backfill = EventBackfill(spool)
    exporter = MetricsExporter(
        config.metrics_host, config.metrics_port, stale_seconds=max(30, config.sample_interval * 3)
    )
    previous, payloads = {}, {}
    last_inventory = last_heartbeat = 0.0
    with httpx.Client(base_url=config.api_base, verify=config.tls_ca, timeout=15) as api:
        try:
            while running:
                started = time.monotonic()
                try:
                    if identity.expired(margin_seconds=60):
                        identity.register()
                        logger.info("Probe registered/renewed %s", identity.probe_id)
                except Exception:
                    logger.exception("Registration unavailable; continue local collection")
                try:
                    instances = discover(client, config.vm_external_id)
                    write_identity(config.identity_path, instances)
                    for raw in backfill.poll(client):
                        mapped = container_event_payload(raw)
                        if mapped and raw.get("container_id"):
                            event_type, payload = mapped
                            spool.enqueue(
                                build_event(
                                    event_type=event_type,
                                    container_id=raw["container_id"],
                                    payload=payload,
                                    source=f"vm-probe:{config.vm_external_id}",
                                    event_id=docker_event_id(raw),
                                    occurred_at=docker_occurred_at(raw),
                                ),
                                priority=PRIORITY_HIGH,
                            )
                    backfill.commit_cursor()
                    samples = []
                    for inst in instances:
                        if inst.attributes["state"] != "running":
                            continue
                        stats = client.container_stats(inst.source_id)
                        _, payload = stats_event_payload(
                            stats,
                            previous=previous.get(inst.attributes["service_instance"]),
                            limits=inst.limits,
                        )
                        previous[inst.attributes["service_instance"]] = stats
                        payload["instance_id"] = inst.attributes["service_instance"]
                        payloads[inst.source_id] = payload
                        spool.enqueue(
                            build_event(
                                event_type="container.stats.sample",
                                container_id=inst.source_id,
                                payload=payload,
                                source=f"vm-probe:{config.vm_external_id}",
                            ),
                            priority=PRIORITY_LOW,
                        )
                        samples.extend(
                            samples_from_stats(
                                inst.source_id,
                                identity.vm_id or config.vm_external_id,
                                payload,
                                probe_id=identity.probe_id or "",
                                instance_id=inst.attributes["service_instance"],
                                vm_external_id=config.vm_external_id,
                            )
                        )
                    exporter.update(samples)
                except Exception:
                    logger.exception("Docker collection failed")
                    instances = None
                if identity.credential and not identity.expired():
                    try:
                        if (
                            instances is not None
                            and started - last_inventory >= config.inventory_interval
                        ):
                            response = api.post(
                                f"/api/v1/probes/{identity.probe_id}/inventory",
                                json=inventory_payload(instances, metrics=payloads),
                                headers=identity.auth_headers(),
                            )
                            response.raise_for_status()
                            last_inventory = started
                        flush_outbox(api, identity, spool)
                        if started - last_heartbeat >= config.heartbeat_interval:
                            response = api.post(
                                f"/api/v1/probes/{identity.probe_id}/heartbeat",
                                json={
                                    "observed_at": datetime.now(UTC).isoformat(),
                                    "queue_depth": spool.depth,
                                    "dropped_count": spool.dropped_count,
                                    "oldest_queued_at": spool.oldest_queued_at,
                                    "clock_offset_seconds": None,
                                    "gaps": spool.gaps,
                                },
                                headers=identity.auth_headers(),
                            )
                            response.raise_for_status()
                            last_heartbeat = started
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (401, 403):
                            identity.expires_at = None
                        logger.exception("Upload failed; SQLite outbox retained")
                    except Exception:
                        logger.exception("Upload failed; SQLite outbox retained")
                wait_until = started + config.sample_interval
                while running:
                    remaining = wait_until - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(0.25, remaining))
        finally:
            client.close()
            spool.close()
            exporter.close()


if __name__ == "__main__":
    main()
