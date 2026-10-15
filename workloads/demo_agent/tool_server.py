"""Local deterministic runbook HTTP service, with an expiring lab fault file."""

from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FAULT_PATH = Path(os.environ.get("TOOL_FAULT_STATE", "/var/lib/layertrace-tool/fault.json"))
RUNBOOKS = {
    "memory": {
        "title": "Memory pressure",
        "checks": [
            "Inspect the container memory limit and OOM flag",
            "Reduce batch size before raising the limit",
        ],
        "command": "docker inspect --format '{{json .State}}' CONTAINER",
    },
    "cpu": {
        "title": "CPU quota bottleneck",
        "checks": [
            "Compare CPU quota and throttled periods",
            "Check VM idle CPU before blaming the host",
        ],
        "command": "docker stats --no-stream CONTAINER",
    },
    "tool": {
        "title": "Unavailable tool",
        "checks": ["Check the tool HTTP response", "Retry only after the dependency is healthy"],
        "command": "curl -f http://TOOL:8090/healthz",
    },
}


def active_fault() -> dict | None:
    try:
        value = json.loads(FAULT_PATH.read_text())
    except (OSError, ValueError):
        return None
    if value.get("expires_at", 0) < time.time():
        return None
    return value


class Handler(BaseHTTPRequestHandler):
    def reply(self, code: int, value: dict):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            self.reply(200, {"status": "up", "fault_active": bool(active_fault())})
        else:
            self.reply(404, {"error": "unknown_route"})

    def do_POST(self):
        if self.path != "/tools/lookup_runbook":
            self.reply(404, {"error": "unknown_tool"})
            return
        size = int(self.headers.get("Content-Length", 0))
        if not 0 < size < 16384:
            self.reply(413, {"error": "invalid_body_size"})
            return
        try:
            args = json.loads(self.rfile.read(size))
        except ValueError:
            self.reply(400, {"error": "invalid_json"})
            return
        fault = active_fault()
        if fault and fault.get("mode") == "http_503":
            self.reply(
                503,
                {
                    "error": "temporarily_unavailable",
                    "run_id": fault.get("run_id"),
                    "retryable": True,
                },
            )
            return
        if fault and fault.get("mode") == "deadline":
            time.sleep(float(fault.get("delay_seconds", 20)))
        topic = args.get("topic", "memory")
        self.reply(
            200,
            {
                "tool": "lookup_runbook",
                "topic": topic,
                "runbook": RUNBOOKS.get(topic, RUNBOOKS["memory"]),
            },
        )

    def log_message(self, format, *args):
        print(json.dumps({"source": "demo-tool", "message": format % args}), flush=True)


def main():
    ThreadingHTTPServer(
        (os.environ.get("TOOL_BIND_HOST", "0.0.0.0"), int(os.environ.get("TOOL_PORT", "8090"))),
        Handler,
    ).serve_forever()


if __name__ == "__main__":
    main()
