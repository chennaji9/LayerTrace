"""Inject real, bounded Docker faults and restore the exact saved configuration."""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

LABEL = "layertrace.lab"
MAX_SECONDS = 180
FAULT_PATH = "/var/lib/layertrace-tool/fault.json"


def now() -> str:
    return datetime.now(UTC).isoformat()


def docker(*args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["docker", *args], input=input_text, text=True, capture_output=True, timeout=30
    )
    if result.returncode:
        raise RuntimeError(f"docker {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


def inspect(target: str) -> dict:
    value = json.loads(docker("inspect", target))[0]
    if (value.get("Config", {}).get("Labels") or {}).get(LABEL) != "true":
        raise PermissionError(f"Target must have {LABEL}=true")
    return value


def safe_snapshot(value: dict) -> dict:
    host, state = value["HostConfig"], value["State"]
    return {
        "container_id": value["Id"],
        "name": value["Name"].lstrip("/"),
        "service": (value["Config"].get("Labels") or {}).get("layertrace.service"),
        "state": {
            key: state.get(key)
            for key in (
                "Status",
                "Running",
                "OOMKilled",
                "ExitCode",
                "StartedAt",
                "FinishedAt",
                "Pid",
            )
        },
        "config": {
            key: host.get(key)
            for key in (
                "NanoCpus",
                "CpuQuota",
                "CpuPeriod",
                "Memory",
                "MemorySwap",
                "RestartPolicy",
            )
        },
    }


def write_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    temporary.replace(path)


@contextmanager
def state_lock(path: Path):
    """Serialize the runner, manual recovery and detached recovery process."""
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def start_watchdog(path: Path) -> subprocess.Popen:
    ready = path.with_name("watchdog-ready.json")
    with path.with_name("watchdog.log").open("a") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "scenarios.real_lab", "watchdog", "--state", str(path)],
            cwd=Path(__file__).resolve().parents[1],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            close_fds=True,
        )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Independent recovery process exited before injection")
        if ready.exists() and json.loads(ready.read_text()).get("pid") == process.pid:
            return process
        time.sleep(0.05)
    process.terminate()
    process.wait(timeout=5)
    raise RuntimeError("Independent recovery process did not arm before injection")


def watchdog(path: Path):
    """Survive runner SIGKILL/terminal loss and restore at the saved deadline."""
    write_state(path.with_name("watchdog-ready.json"), {"pid": os.getpid(), "ready_at": now()})
    while True:
        state = json.loads(path.read_text())
        if state.get("state") == "restored" and state.get("restore_verified"):
            return
        remaining = state["deadline_monotonic"] - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.5, remaining))
    try:
        restored = restore(path)
        write_state(path.with_name("watchdog-result.json"), restored)
        print(json.dumps({"run_id": restored["run_id"], "restore_verified": True}), flush=True)
    except Exception as exc:
        write_state(
            path.with_name("watchdog-result.json"),
            {"restore_verified": False, "error": f"{type(exc).__name__}: {exc}", "at": now()},
        )
        raise


def tool_file(container_id: str, *, write: str | None = None, remove: bool = False) -> str | None:
    # The code is fixed and arguments travel as argv, never through a shell.
    if write is not None:
        code = "import pathlib,sys,base64;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(sys.argv[2]))"
        return docker(
            "exec",
            container_id,
            "python",
            "-c",
            code,
            FAULT_PATH,
            base64.b64encode(write.encode()).decode(),
        )
    if remove:
        return docker(
            "exec",
            container_id,
            "python",
            "-c",
            "import pathlib,sys;pathlib.Path(sys.argv[1]).unlink(missing_ok=True)",
            FAULT_PATH,
        )
    output = docker(
        "exec",
        container_id,
        "python",
        "-c",
        "import pathlib,sys,base64;p=pathlib.Path(sys.argv[1]);print(base64.b64encode(p.read_bytes()).decode() if p.exists() else 'ABSENT')",
        FAULT_PATH,
    ).strip()
    return None if output == "ABSENT" else base64.b64decode(output).decode()


def restore(path: Path) -> dict:
    with state_lock(path):
        return _restore_locked(path)


def _restore_locked(path: Path) -> dict:
    state = json.loads(path.read_text())
    if state.get("state") == "restored":
        return state
    cid = state["before"]["container_id"]
    current = inspect(cid)  # Never fall back to a same-named replacement.
    mode, before = state["mode"], state["before"]
    state["state"] = "restoring"
    write_state(path, state)
    if mode == "cpu-quota":
        config = before["config"]
        if config.get("NanoCpus"):
            docker("update", "--cpus", str(config["NanoCpus"] / 1e9), cid)
        else:
            docker(
                "update",
                "--cpu-period",
                str(config.get("CpuPeriod") or 100000),
                "--cpu-quota",
                str(config.get("CpuQuota") or 0),
                cid,
            )
    elif mode == "tool-503":
        old = state.get("tool_fault_before")
        tool_file(cid, write=old) if old is not None else tool_file(cid, remove=True)
    elif mode == "process-failure":
        policy = before["config"].get("RestartPolicy") or {}
        restart = policy.get("Name") or "no"
        if restart == "on-failure" and policy.get("MaximumRetryCount"):
            restart += ":" + str(policy["MaximumRetryCount"])
        docker("update", "--restart", restart, cid)
        if before["state"]["Running"] and not current["State"]["Running"]:
            docker("start", cid)
    after = safe_snapshot(inspect(cid))
    expected, actual = before["config"], after["config"]
    if mode == "cpu-quota":
        keys = ("NanoCpus", "CpuQuota", "CpuPeriod")
        # Docker stores an unset period as 0; a 100000us period is equivalent but
        # exact configuration is restored below with the Docker Engine API.
        if any(expected.get(k) != actual.get(k) for k in keys):
            _restore_cpu_api(cid, expected)
            after = safe_snapshot(inspect(cid))
            actual = after["config"]
        restored = all(expected.get(k) == actual.get(k) for k in keys)
    elif mode == "process-failure":
        restored = expected["RestartPolicy"] == actual["RestartPolicy"] and bool(
            after["state"]["Running"]
        ) == bool(before["state"]["Running"])
    else:
        restored = tool_file(cid) == state.get("tool_fault_before")
    state["after"] = after
    state["restore_verified"] = restored
    state["restored_at"] = now()
    state["state"] = "restored" if restored else "restore_failed"
    write_state(path, state)
    if not restored:
        raise RuntimeError(f"Restoration mismatch; inspect {path}")
    return state


def _restore_cpu_api(cid: str, expected: dict):
    import http.client
    import os
    import socket

    class UnixConnection(http.client.HTTPConnection):
        def connect(self):
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.connect(
                os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock").removeprefix("unix://")
            )

    payload = json.dumps(
        {key: expected.get(key) or 0 for key in ("NanoCpus", "CpuQuota", "CpuPeriod")}
    )
    connection = UnixConnection("localhost", timeout=20)
    connection.request(
        "POST", f"/containers/{cid}/update", payload, {"Content-Type": "application/json"}
    )
    response = connection.getresponse()
    data = response.read()
    connection.close()
    if response.status >= 300:
        raise RuntimeError(f"Docker CPU configuration restore failed: {data.decode()}")


def run(mode: str, target: str, seconds: int, cpus: float, output: Path) -> dict:
    if not 1 <= seconds <= MAX_SECONDS:
        raise ValueError(f"Duration must be 1..{MAX_SECONDS} seconds")
    if not 0.01 <= cpus <= 2:
        raise ValueError("Fault CPU quota must be 0.01..2 cores")
    value = inspect(target)
    if not value["State"]["Running"]:
        raise ValueError("A running lab container is required")
    before = safe_snapshot(value)
    expected_service = {
        "process-failure": "demo-agent",
        "cpu-quota": "demo-model",
        "tool-503": "demo-tool",
    }[mode]
    if before["service"] != expected_service:
        raise ValueError(f"{mode} requires layertrace.service={expected_service}")
    if mode == "cpu-quota" and not (
        before["config"].get("NanoCpus")
        or (before["config"].get("CpuQuota", 0) > 0 and before["config"].get("CpuPeriod", 0) > 0)
    ):
        raise ValueError(
            "CPU laboratory target must have an explicit original CPU limit for exact restoration"
        )
    run_id = "lab-" + uuid.uuid4().hex[:12]
    folder = (output / run_id).resolve()
    path = folder / "state.json"
    state = {
        "run_id": run_id,
        "mode": mode,
        "state": "prepared",
        "started_at": now(),
        "seconds": seconds,
        "before": before,
        # The initial grace is only for watchdog startup, before any mutation.
        "deadline_monotonic": time.monotonic() + seconds + 5,
    }
    if mode == "tool-503":
        state["tool_fault_before"] = tool_file(before["container_id"])
    write_state(path, state)
    print(json.dumps({"run_id": run_id, "state_file": str(path), "status": "prepared"}), flush=True)
    original_handlers = {}

    def interrupted(signum, _frame):
        raise KeyboardInterrupt(f"Signal {signum}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        original_handlers[signum] = signal.signal(signum, interrupted)
    observations = []
    recovery_process = None
    restoration_complete = False
    try:
        recovery_process = start_watchdog(path)
        cid = before["container_id"]
        with state_lock(path):
            state = json.loads(path.read_text())
            if state.get("state") == "restored":
                raise RuntimeError("Recovery deadline elapsed before injection")
            deadline = time.monotonic() + seconds
            state.update(
                state="injecting",
                deadline_monotonic=deadline,
                deadline_at=datetime.fromtimestamp(time.time() + seconds, UTC).isoformat(),
                watchdog={"pid": recovery_process.pid, "mode": "detached-process"},
            )
            write_state(path, state)
            if mode == "process-failure":
                docker("update", "--restart", "no", cid)
                docker("kill", "--signal", "KILL", cid)
            elif mode == "cpu-quota":
                # Preserve the same Engine resource field; NanoCpus and legacy quota
                # cannot coexist, and an unset original field must stay unset.
                if before["config"].get("NanoCpus"):
                    docker("update", "--cpus", str(cpus), cid)
                else:
                    docker(
                        "update",
                        "--cpu-period",
                        str(before["config"]["CpuPeriod"]),
                        "--cpu-quota",
                        str(max(1000, int(cpus * before["config"]["CpuPeriod"]))),
                        cid,
                    )
            else:
                tool_file(
                    cid,
                    write=json.dumps(
                        {"mode": "http_503", "run_id": run_id, "expires_at": time.time() + seconds}
                    ),
                )
            state["state"] = "running"
            state["injected_at"] = now()
            state["during"] = safe_snapshot(inspect(cid))
            write_state(path, state)
        print(
            json.dumps({"run_id": run_id, "status": "running", "restore_after_seconds": seconds}),
            flush=True,
        )
        while time.monotonic() < deadline:
            snapshot = safe_snapshot(inspect(cid))
            observation = {"observed_at": now(), "snapshot": snapshot}
            if mode == "cpu-quota" and snapshot["state"]["Running"]:
                observation["docker_stats"] = json.loads(
                    docker("stats", "--no-stream", "--format", "{{json .}}", cid)
                )
            if mode == "tool-503":
                code = "import urllib.request,urllib.error,json;r=urllib.request.Request('http://127.0.0.1:8090/tools/lookup_runbook',data=b'{\"topic\":\"memory\"}',headers={'Content-Type':'application/json'});\ntry:\n x=urllib.request.urlopen(r,timeout=3);print(json.dumps({'http_status':x.status}))\nexcept urllib.error.HTTPError as e:\n print(json.dumps({'http_status':e.code}))"
                observation["http"] = json.loads(docker("exec", cid, "python", "-c", code))
            observations.append(observation)
            time.sleep(min(5, max(0, deadline - time.monotonic())))
    except BaseException as exc:
        with state_lock(path):
            state = json.loads(path.read_text())
            state["run_error"] = type(exc).__name__ + ": " + str(exc)
            write_state(path, state)
        if not isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        try:
            restored = restore(path)
            restoration_complete = bool(restored.get("restore_verified"))
            restored["observations"] = observations
            write_state(folder / "evidence.json", restored)
            print(
                json.dumps(
                    {
                        "run_id": run_id,
                        "status": restored["state"],
                        "restore_verified": restored["restore_verified"],
                        "evidence": str(folder / "evidence.json"),
                    }
                ),
                flush=True,
            )
        finally:
            if (
                restoration_complete
                and recovery_process is not None
                and recovery_process.poll() is None
            ):
                recovery_process.terminate()
                recovery_process.wait(timeout=5)
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    runner = sub.add_parser("run")
    runner.add_argument("mode", choices=["process-failure", "cpu-quota", "tool-503"])
    runner.add_argument("--target", required=True)
    runner.add_argument("--seconds", type=int, default=45)
    runner.add_argument("--cpus", type=float, default=0.1)
    runner.add_argument("--output", type=Path, default=Path("evidence/lab"))
    restorer = sub.add_parser("restore")
    restorer.add_argument("--state", type=Path, required=True)
    recovery = sub.add_parser("watchdog", help=argparse.SUPPRESS)
    recovery.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "restore":
        print(json.dumps(restore(args.state), ensure_ascii=False))
    elif args.action == "watchdog":
        watchdog(args.state)
    else:
        run(args.mode, args.target, args.seconds, args.cpus, args.output)


if __name__ == "__main__":
    main()
