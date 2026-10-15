"""Prometheus pull exporter with consistent full resource identities."""

from __future__ import annotations

import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

METRICS_HELP = {
    "probe_container_cpu_percent": "Container CPU percent (100 means one core)",
    "probe_container_memory_working_set_bytes": "Container memory working set",
    "container_memory_working_set_bytes": "Container memory working set compatibility metric",
    "probe_container_memory_limit_bytes": "Container configured memory limit",
    "probe_container_cpu_limit_cores": "Container configured CPU cores",
    "probe_container_throttled_periods_total": "Container throttled CPU periods",
    "probe_container_cpu_periods_total": "Container total CPU periods",
    "probe_container_throttled_seconds_total": "Container CPU throttled seconds",
    "probe_container_throttled_ratio": "Throttled periods divided by total periods in sample",
}


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def render_prometheus(samples: list[dict], labels_for) -> str:
    lines, emitted = [], set()
    for sample in samples:
        name = sample["metric"]
        value = float(sample["value"])
        if not math.isfinite(value):
            continue
        if name not in emitted:
            lines.extend(
                (
                    f"# HELP {name} {METRICS_HELP.get(name, name)}",
                    f"# TYPE {name} {'counter' if name.endswith('_total') else 'gauge'}",
                )
            )
            emitted.add(name)
        label_str = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels_for(sample).items()))
        lines.append(f"{name}{{{label_str}}} {value}")
    return "\n".join(lines) + "\n"


def samples_from_stats(
    container_id: str,
    vm_id: str,
    payload: dict,
    *,
    probe_id: str = "",
    instance_id: str = "",
    vm_external_id: str = "",
) -> list[dict]:
    labels = {
        "probe_id": probe_id,
        "vm_id": vm_id,
        "vm_external_id": vm_external_id,
        "container_id": container_id,
        "instance_id": instance_id,
    }
    names = {
        "probe_container_cpu_percent": ("cpu_percent", 1),
        "probe_container_memory_working_set_bytes": ("memory_working_set_bytes", 1),
        "container_memory_working_set_bytes": ("memory_working_set_bytes", 1),
        "probe_container_memory_limit_bytes": ("memory_limit_bytes", 1),
        "probe_container_cpu_limit_cores": ("cpu_limit_cores", 1),
        "probe_container_throttled_periods_total": ("throttled_periods", 1),
        "probe_container_cpu_periods_total": ("cpu_periods", 1),
        "probe_container_throttled_seconds_total": ("throttled_time_ns", 1e-9),
        "probe_container_throttled_ratio": ("throttled_ratio", 1),
    }
    return [
        {"metric": name, "value": float(payload.get(key) or 0) * scale, "labels": labels}
        for name, (key, scale) in names.items()
    ]


class MetricsExporter:
    def __init__(self, host: str, port: int, *, stale_seconds: int = 30) -> None:
        self.samples: list[dict] = []
        self.updated_at = 0.0
        self.stale_seconds = stale_seconds
        self.lock = threading.Lock()
        exporter = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                with exporter.lock:
                    current = time.time() - exporter.updated_at <= exporter.stale_seconds
                    body = render_prometheus(
                        exporter.samples if current else [], lambda sample: sample["labels"]
                    )
                    body += f"probe_up {int(current)}\n"
                    body += f"probe_last_collection_unixtime {exporter.updated_at}\n"
                encoded = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; version=0.0.4")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def update(self, samples: list[dict]) -> None:
        with self.lock:
            self.samples = samples
            self.updated_at = time.time()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
