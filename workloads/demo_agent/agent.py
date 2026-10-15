"""Real CPU inference -> HTTP runbook -> model synthesis; every phase shares task_id."""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from workloads.demo_agent import events as ev
from workloads.demo_agent.tool import call_tool

MODEL_URL = os.environ.get("LLAMA_URL", "http://127.0.0.1:8080").rstrip("/")
MODEL_TIMEOUT = float(os.environ.get("MODEL_TIMEOUT_SECONDS", "45"))
TOOL_TIMEOUT = float(os.environ.get("TOOL_TIMEOUT_SECONDS", "5"))
logger = logging.getLogger("layertrace.demo-agent")


def _agent_timeout_ms() -> int:
    """A configurable ceiling keeps an overloaded model bounded."""
    return int(MODEL_TIMEOUT * 1000)


def _completion(prompt: str, tokens: int, timeout: float) -> str:
    response = httpx.post(
        f"{MODEL_URL}/completion",
        json={
            "prompt": "<|im_start|>system\nYou are an operations assistant. Answer briefly and accurately.<|im_end|>\n<|im_start|>user\n"
            + prompt
            + "<|im_end|>\n<|im_start|>assistant\n",
            "n_predict": tokens,
            "temperature": 0,
            "seed": 17,
            "cache_prompt": False,
            "stop": ["<|im_end|>", "<|endoftext|>"],
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json().get("content", "").strip()
    if not content:
        raise RuntimeError("Model produced an empty response")
    return content


def run_task(task_id: str | None = None, *, topic: str = "memory") -> dict:
    task_id = task_id or f"task-{uuid.uuid4().hex}"
    agent_identity = ev.service_identity("demo-agent", require_current=True)
    model_identity = ev.service_identity("demo-model")
    tool_identity = ev.service_identity("demo-tool")
    started = time.monotonic()
    model_ms = tool_ms = 0
    phase = "model"
    first_answer = summary = ""
    tool_result = {}
    error_class = None
    phase_started = started
    attrs = {
        "observer": "agent",
        "model_instance_id": model_identity["instance_id"],
        "tool_instance_id": tool_identity["instance_id"],
    }
    ev.emit_task_event("task.started", task_id, attrs, instance_id=agent_identity["instance_id"])
    try:
        ev.emit_task_event(
            "model.started",
            task_id,
            {"phase": "initial"},
            service="demo-model",
            instance_id=model_identity["instance_id"],
        )
        phase_started = time.monotonic()
        first_answer = _completion(
            f"Explain why a low {topic} resource limit can cause a Linux container to fail or slow down. One sentence, at most 15 words.",
            int(os.environ.get("MODEL_INITIAL_TOKENS", "32")),
            MODEL_TIMEOUT,
        )
        model_ms += int((time.monotonic() - phase_started) * 1000)
        ev.emit_task_event(
            "model.completed",
            task_id,
            {"duration_ms": model_ms, "phase": "initial"},
            service="demo-model",
            instance_id=model_identity["instance_id"],
        )
        phase = "tool"
        phase_started = time.monotonic()
        ev.emit_task_event(
            "tool.started",
            task_id,
            {"tool_name": "lookup_runbook"},
            service="demo-tool",
            instance_id=tool_identity["instance_id"],
        )
        tool_result = call_tool(
            "lookup_runbook", {"topic": topic}, timeout=TOOL_TIMEOUT, task_id=task_id
        )
        tool_ms = int((time.monotonic() - phase_started) * 1000)
        ev.emit_task_event(
            "tool.completed",
            task_id,
            {"tool_name": "lookup_runbook", "duration_ms": tool_ms},
            service="demo-tool",
            instance_id=tool_identity["instance_id"],
        )
        phase = "model"
        phase_started = time.monotonic()
        ev.emit_task_event(
            "model.started",
            task_id,
            {"phase": "synthesis"},
            service="demo-model",
            instance_id=model_identity["instance_id"],
        )
        summary = _completion(
            "Write exactly one imperative recommendation of at most 20 words using these facts. Observation: "
            + first_answer
            + ". Runbook: "
            + json.dumps(tool_result["runbook"])
            + ".",
            int(os.environ.get("MODEL_SUMMARY_TOKENS", "40")),
            MODEL_TIMEOUT,
        )
        duration = int((time.monotonic() - phase_started) * 1000)
        model_ms += duration
        ev.emit_task_event(
            "model.completed",
            task_id,
            {"duration_ms": duration, "phase": "synthesis"},
            service="demo-model",
            instance_id=model_identity["instance_id"],
        )
        status = "succeeded"
    except Exception as exc:
        elapsed = int((time.monotonic() - phase_started) * 1000)
        error_class = (
            ("model_call_timeout" if phase == "model" else "tool_timeout")
            if isinstance(exc, httpx.TimeoutException)
            else (
                "tool_http_503"
                if phase == "tool"
                and isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code == 503
                else f"{phase}_{type(exc).__name__}"
            )
        )
        if phase == "model":
            model_ms += elapsed
            ev.emit_task_event(
                "model.failed",
                task_id,
                {"duration_ms": elapsed, "error_class": error_class},
                service="demo-model",
                instance_id=model_identity["instance_id"],
            )
        else:
            tool_ms += elapsed
            ev.emit_task_event(
                "tool.failed",
                task_id,
                {
                    "duration_ms": elapsed,
                    "tool_name": "lookup_runbook",
                    "error_class": error_class,
                    "http_status": exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError)
                    else None,
                },
                service="demo-tool",
                instance_id=tool_identity["instance_id"],
            )
        status = (
            "timed_out"
            if phase == "model" and isinstance(exc, httpx.TimeoutException)
            else "failed"
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    telemetry = {
        **attrs,
        "duration_ms": duration_ms,
        "model_duration_ms": model_ms,
        "tool_duration_ms": tool_ms,
        "error_class": error_class,
        "phase": phase,
    }
    ev.emit_task_event(
        f"task.{status}", task_id, telemetry, instance_id=agent_identity["instance_id"]
    )
    return {
        "task_id": task_id,
        "status": status,
        **telemetry,
        "model_answer": first_answer,
        "tool_result": tool_result,
        "summary": summary,
        "instance_id": agent_identity["instance_id"],
    }


class Handler(BaseHTTPRequestHandler):
    def reply(self, status: int, value: dict):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/healthz":
            try:
                identity = ev.service_identity("demo-agent", require_current=True)
                self.reply(200, {"status": "ready", "instance_id": identity["instance_id"]})
            except RuntimeError:
                self.reply(503, {"status": "waiting_for_probe_identity"})
        else:
            self.reply(404, {"error": "unknown_route"})

    def do_POST(self):
        if self.path != "/tasks":
            self.reply(404, {"error": "unknown_route"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 16384:
                self.reply(413, {"error": "invalid_body_size"})
                return
            body = json.loads(self.rfile.read(size))
            task_id = body.get("task_id")
            if not isinstance(task_id, str) or not 1 <= len(task_id) <= 256:
                self.reply(400, {"error": "task_id_required"})
                return
            self.reply(200, run_task(task_id, topic=body.get("topic", "memory")))
        except RuntimeError as exc:
            self.reply(503, {"error": str(exc)})
        except (ValueError, TypeError):
            self.reply(400, {"error": "invalid_json"})

    def log_message(self, *_args):
        pass


def main():
    logging.basicConfig(level=logging.INFO)
    ev.wait_for_identity(require_current=True)
    deadline = time.monotonic() + 90
    while True:
        try:
            model = httpx.get(f"{MODEL_URL}/health", timeout=2)
            tool = httpx.get(
                os.environ.get("TOOL_SERVER_URL", "http://127.0.0.1:8090").rstrip("/") + "/healthz",
                timeout=2,
            )
            if model.status_code == tool.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError("Model/tool did not become ready during startup")
        time.sleep(0.5)
    ThreadingHTTPServer(
        (os.environ.get("AGENT_BIND_HOST", "0.0.0.0"), int(os.environ.get("AGENT_PORT", "8091"))),
        Handler,
    ).serve_forever()


if __name__ == "__main__":
    main()
