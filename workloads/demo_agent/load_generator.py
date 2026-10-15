"""Independent client observes agent process/network failure and persists results."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from workloads.demo_agent import events as ev

INTERVAL_SECONDS = float(os.environ.get("LOAD_INTERVAL_SECONDS", "2"))
PAUSE_FILE = os.environ.get("LOAD_PAUSE_FILE", "/results/pause")
AGENT_URL = os.environ.get("AGENT_URL", "http://demo-agent:8091")
CLIENT_TIMEOUT = float(os.environ.get("CLIENT_TIMEOUT_SECONDS", "100"))
RESULTS_PATH = Path(os.environ.get("LOAD_RESULTS_PATH", "/results/tasks.jsonl"))
_running = True


def _handle_stop(*_args):
    global _running
    _running = False


def paused() -> bool:
    return os.path.exists(PAUSE_FILE)


def run_client_task(*, topic: str = "memory") -> dict:
    task_id = "task-" + uuid.uuid4().hex
    identity = ev.wait_for_identity("demo-agent", timeout=30)
    started = time.monotonic()
    ev.emit_task_event(
        "task.started",
        task_id,
        {"observer": "independent-client"},
        instance_id=identity["instance_id"],
    )
    try:
        response = httpx.post(
            AGENT_URL.rstrip("/") + "/tasks",
            json={"task_id": task_id, "topic": topic},
            timeout=CLIENT_TIMEOUT,
        )
        response.raise_for_status()
        result = response.json()
    except Exception as exc:
        duration_ms = int((time.monotonic() - started) * 1000)
        error_class = (
            "agent_unreachable" if isinstance(exc, httpx.ConnectError) else "agent_request_failed"
        )
        ev.emit_task_event(
            "task.failed",
            task_id,
            {
                "observer": "independent-client",
                "error_class": error_class,
                "duration_ms": duration_ms,
                "http_status": exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None,
            },
            instance_id=identity["instance_id"],
        )
        result = {
            "task_id": task_id,
            "status": "failed",
            "error_class": error_class,
            "duration_ms": duration_ms,
            "instance_id": identity["instance_id"],
        }
    result["client_observed_at"] = datetime.now(UTC).isoformat()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS_PATH.open("a", encoding="utf-8") as output:
        output.write(json.dumps(result, ensure_ascii=False) + "\n")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=0, help="0 runs continuously")
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS)
    parser.add_argument("--topic", default="memory")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    startup_deadline = time.monotonic() + 90
    while _running:
        try:
            if httpx.get(AGENT_URL.rstrip("/") + "/healthz", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() >= startup_deadline:
            raise RuntimeError("Agent did not become ready during startup")
        time.sleep(0.5)
    completed = 0
    while _running and (not args.count or completed < args.count):
        if not paused():
            run_client_task(topic=args.topic)
            completed += 1
        deadline = time.monotonic() + args.interval
        while _running:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.25, remaining))
    try:
        while ev.emitter().flush():
            pass
    except Exception:
        pass


if __name__ == "__main__":
    main()
