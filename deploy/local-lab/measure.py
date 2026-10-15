#!/usr/bin/env python3
"""Measure probe resources and existing business tasks on the actual Linux VM."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

PROBE = "layertrace-vm-probe"
PROXY = "layertrace-docker-proxy"
BUSINESS = ("layertrace-demo-model", "layertrace-demo-agent", "layertrace-demo-tool")


def run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError(f"{args[0]} {args[1]} failed (exit {result.returncode})")
    return result.stdout


def inspect(target: str) -> dict:
    return json.loads(run(["docker", "inspect", target]))[0]


def save(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


@contextmanager
def locked(path: Path):
    with path.with_suffix(".lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def group_path(pid: int) -> str:
    for line in (Path("/proc") / str(pid) / "cgroup").read_text().splitlines():
        if line.startswith("0::"):
            value = line[3:].lstrip("/")
            if not value or ".." in Path(value).parts:
                break
            return value
    raise RuntimeError("Measurement requires a non-root cgroup v2 container path")


def identity(value: dict) -> dict:
    return {
        "container_id": value["Id"],
        "started_at": value["State"]["StartedAt"],
        "pid": value["State"]["Pid"],
        "paused": value["State"]["Paused"],
        "running": value["State"]["Running"],
    }


def probe_identity(target: str) -> dict:
    value = inspect(target)
    expected = "agents.vm_probe" if target == PROBE else "agents.vm_probe.socket_proxy"
    if value["Name"].lstrip("/") != target or expected not in value["Config"]["Cmd"]:
        raise RuntimeError("Measurement target is not the expected LayerTrace component")
    if not value["State"]["Running"] or value["State"]["Paused"]:
        raise RuntimeError("Measurement starts with both probe components running and unpaused")
    return {**identity(value), "cgroup": group_path(value["State"]["Pid"])}


def business_identity() -> dict:
    values = {name: identity(inspect(name)) for name in BUSINESS}
    if any(not value["running"] or value["paused"] for value in values.values()):
        raise RuntimeError("Business containers must be running before measurement")
    return values


def continuous_client(value: dict):
    command = value["Config"]["Cmd"] or []
    if "workloads.demo_agent.load_generator" not in command:
        raise RuntimeError("Measurement requires the standard continuous demo client")
    for index, argument in enumerate(command):
        if argument == "--count":
            if index + 1 >= len(command) or command[index + 1] != "0":
                raise RuntimeError("A finite business client cannot span both measurement windows")
        elif argument.startswith("--count=") and argument != "--count=0":
            raise RuntimeError("A finite business client cannot span both measurement windows")


def spool_snapshot(path: Path) -> dict:
    """Read only aggregate outbox facts; never archive events, credentials or SQLite."""
    with closing(
        sqlite3.connect((path / "outbox.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=5)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        depth, oldest = db.execute("SELECT COUNT(*),MIN(enqueued_at) FROM outbox").fetchone()
        meta = {
            key: json.loads(value)
            for key, value in db.execute(
                "SELECT key,value FROM meta WHERE key IN ('dropped_count','gaps')"
            )
        }
    return {
        "at": datetime.now(UTC).isoformat(),
        "depth": depth,
        "oldest_queued_at": datetime.fromtimestamp(oldest, UTC).isoformat()
        if oldest is not None
        else None,
        "oldest_age_seconds": max(0, time.time() - oldest) if oldest is not None else None,
        "dropped_count": int(meta.get("dropped_count", 0)),
        "gaps": meta.get("gaps", []),
    }


def counters(value: dict) -> dict:
    group = Path("/sys/fs/cgroup") / value["cgroup"]
    cpu = dict(line.split() for line in (group / "cpu.stat").read_text().splitlines())
    disk = {"rbytes": 0, "wbytes": 0}
    for line in (group / "io.stat").read_text().splitlines():
        parts = dict(item.split("=", 1) for item in line.split()[1:])
        for key in disk:
            disk[key] += int(parts.get(key, 0))
    process_paths = [group / "cgroup.procs", *group.rglob("cgroup.procs")]
    pids = {pid for path in process_paths for pid in path.read_text().splitlines()}
    rss = 0
    io_totals = dict.fromkeys(("read_bytes", "write_bytes", "wchar", "cancelled_write_bytes"), 0)
    for pid in pids:
        process = Path("/proc") / pid
        try:
            status = dict(
                line.split(":", 1)
                for line in (process / "status").read_text().splitlines()
                if ":" in line
            )
            io = dict(line.split(":", 1) for line in (process / "io").read_text().splitlines())
        except FileNotFoundError:
            continue
        rss += int(status.get("VmRSS", "0").split()[0]) * 1024
        for key in io_totals:
            io_totals[key] += int(io.get(key, 0))
    return {
        "cpu_usage_usec": int(cpu["usage_usec"]),
        "cpu_user_usec": int(cpu.get("user_usec", 0)),
        "cpu_system_usec": int(cpu.get("system_usec", 0)),
        "rss_bytes": rss,
        "cgroup_memory_current_bytes": int((group / "memory.current").read_text()),
        "cgroup_read_bytes": disk["rbytes"],
        "cgroup_write_bytes": disk["wbytes"],
        "process_read_bytes": io_totals["read_bytes"],
        "process_write_bytes": io_totals["write_bytes"],
        "process_wchar": io_totals["wchar"],
        "process_cancelled_write_bytes": io_totals["cancelled_write_bytes"],
    }


def machine_memory() -> dict:
    fields = {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}
    return {
        key: int(value.split()[0]) * 1024
        for line in Path("/proc/meminfo").read_text().splitlines()
        for key, value in [line.split(":", 1)]
        if key in fields
    }


def cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return "unknown"


def volume(target: str, destination: str) -> Path:
    value = inspect(target)
    matches = [Path(v["Source"]) for v in value["Mounts"] if v["Destination"] == destination]
    if len(matches) != 1 or not matches[0].is_dir():
        raise RuntimeError(f"Verified {destination} state volume was not found")
    return matches[0]


def disk_usage(path: Path) -> dict:
    sizes = []
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file():
                sizes.append(candidate.stat())
        except FileNotFoundError:
            # Atomic cache/WAL replacements may disappear between directory walk and stat.
            continue
    return {
        "logical_bytes": sum(s.st_size for s in sizes),
        "allocated_bytes": sum(s.st_blocks * 512 for s in sizes),
    }


def network_setup(state: dict):
    chain = state["network_chain"]
    if not re.fullmatch(r"LTPM[0-9a-f]{12}", chain):
        raise ValueError("Invalid dedicated accounting chain")
    run(["iptables", "-N", chain])
    for rule in (
        ["-p", "tcp", "-d", "127.0.0.1", "--dport", "18000"],
        ["-p", "tcp", "-s", "127.0.0.1", "--sport", "9108"],
        [],
    ):
        run(["iptables", "-A", chain, *rule, "-j", "RETURN"])
    run(["iptables", "-A", "OUTPUT", *state["network_match"], "-j", chain])


def network_read(state: dict) -> dict:
    text = run(["iptables", "-n", "-v", "-x", "-L", state["network_chain"]])
    rows = [
        line.split()[:2] for line in text.splitlines() if re.match(r"\s*\d+\s+\d+\s+RETURN\b", line)
    ]
    if len(rows) != 3:
        raise RuntimeError("Probe accounting counter shape changed")
    return {
        "api_upload_ip_bytes": int(rows[0][1]),
        "metrics_response_ip_bytes": int(rows[1][1]),
        "other_probe_ip_bytes": int(rows[2][1]),
    }


def network_remove(state: dict):
    chain = state["network_chain"]
    if not re.fullmatch(r"LTPM[0-9a-f]{12}", chain):
        raise ValueError("Invalid dedicated accounting chain")
    deletion = ["iptables", "-D", "OUTPUT", *state["network_match"], "-j", chain]
    subprocess.run(deletion, capture_output=True, timeout=30)
    for action in ("-F", "-X"):
        result = subprocess.run(
            ["iptables", action, chain], capture_output=True, text=True, timeout=30
        )
        if (
            result.returncode
            and "No chain" not in result.stderr
            and "does not exist" not in result.stderr
        ):
            raise RuntimeError("Dedicated accounting chain cleanup failed")


def restore(path: Path):
    with locked(path):
        state = json.loads(path.read_text())
        if state.get("restored"):
            return
        failures = []
        # Inspect the saved full IDs, so a same-named replacement is never touched.
        for name in reversed(state.get("paused_by_measurement", [])):
            saved = state["components"][name]
            try:
                current = inspect(saved["container_id"])
                if current["Name"].lstrip("/") != name:
                    raise RuntimeError("Measurement component was replaced")
                if current["State"]["Paused"]:
                    run(["docker", "unpause", saved["container_id"]])
                verified = inspect(saved["container_id"])
                if (
                    verified["Name"].lstrip("/") != name
                    or not verified["State"]["Running"]
                    or verified["State"]["Paused"]
                ):
                    raise RuntimeError("Measurement component recovery was not verified")
            except Exception as exc:
                failures.append(type(exc).__name__)
        try:
            network_remove(state)
        except Exception as exc:
            failures.append(type(exc).__name__)
        if failures:
            raise RuntimeError("Measurement recovery remains incomplete: " + ", ".join(failures))
        state["restored"] = True
        state["restored_at"] = datetime.now(UTC).isoformat()
        save(path, state)


def watchdog(path: Path):
    save(path.with_name("watchdog-ready.json"), {"pid": os.getpid()})
    while True:
        state = json.loads(path.read_text())
        if state.get("restored"):
            return
        remaining = state["deadline_monotonic"] - time.monotonic()
        if remaining <= 0:
            restore(path)
            return
        time.sleep(min(0.5, remaining))


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(q * (len(ordered) - 1))]


def task_stats(path: Path, window_start: float, window_end: float) -> dict:
    rows, excluded = [], 0
    if not path.exists():
        raise RuntimeError(
            "Existing client result file does not exist; the measurement never starts a client"
        )
    with path.open() as log:
        lines = list(log)
    for line in lines:
        try:
            row = json.loads(line)
            end = datetime.fromisoformat(row["client_observed_at"]).timestamp()
            start = end - (row.get("duration_ms") or 0) / 1000
        except (ValueError, KeyError, TypeError):
            continue
        if window_start <= start <= end <= window_end:
            rows.append(
                {
                    k: row.get(k)
                    for k in (
                        "task_id",
                        "status",
                        "duration_ms",
                        "model_duration_ms",
                        "tool_duration_ms",
                        "instance_id",
                        "error_class",
                        "client_observed_at",
                    )
                }
            )
        elif window_start <= end <= window_end:
            excluded += 1
    successes = [r for r in rows if r["status"] == "succeeded"]
    durations = {}
    for field in ("duration_ms", "model_duration_ms", "tool_duration_ms"):
        values = [r[field] for r in successes if isinstance(r.get(field), int | float)]
        durations[field] = {
            "count": len(values),
            "p50": percentile(values, 0.5),
            "p95": percentile(values, 0.95),
            "mean": sum(values) / len(values) if values else None,
        }
    return {
        "all_count": len(rows),
        "succeeded_count": len(successes),
        "boundary_excluded_count": excluded,
        "statuses": {
            status: sum(r["status"] == status for r in rows)
            for status in sorted({r["status"] for r in rows})
        },
        "latencies": durations,
        "tasks": rows,
    }


def measure_window(mode: str, seconds: int, interval: int, output: Path):
    if os.geteuid() != 0:
        raise RuntimeError(
            "Use sudo to read cgroup/process I/O and pause only the probe components"
        )
    if not 10 <= seconds <= 3600 or not 1 <= interval <= 30:
        raise ValueError("Duration must be 10..3600 seconds and sampling interval 1..30")
    for path in (Path(__file__).resolve().parents[2] / "evidence/lab").rglob("state.json"):
        recorded = json.loads(path.read_text())
        if (
            recorded.get("mode") in {"process-failure", "cpu-quota", "tool-503"}
            and recorded.get("state") != "restored"
        ):
            raise RuntimeError("An unrestored laboratory fault prevents comparable measurement")
    client_ids = run(
        ["docker", "ps", "-q", "--filter", "label=layertrace.service=demo-client"]
    ).split()
    if len(client_ids) != 1:
        raise RuntimeError("Measurement requires exactly one already-running business client")
    client = inspect(client_ids[0])
    continuous_client(client)
    if client["State"]["Paused"]:
        raise RuntimeError("Existing business client is paused")
    results = volume(client_ids[0], "/results") / "tasks.jsonl"
    if not results.is_file() or (results.parent / "pause").exists():
        raise RuntimeError("The installed client result file is absent or its load is paused")
    probe_state = volume(PROBE, "/var/lib/layertrace")
    run_id = "overhead-" + mode + "-" + uuid.uuid4().hex[:12]
    folder = (output / run_id).resolve()
    folder.mkdir(parents=True)
    state_path = folder / "state.json"
    components = {name: probe_identity(name) for name in (PROBE, PROXY)}
    business = business_identity()
    spool_before = spool_snapshot(probe_state)
    state = {
        "run_id": run_id,
        "mode": mode,
        "components": components,
        "network_chain": "LTPM" + uuid.uuid4().hex[:12],
        "network_match": ["-m", "cgroup", "--path", components[PROBE]["cgroup"]],
        "deadline_monotonic": time.monotonic() + seconds + 60,
        "paused_by_measurement": [],
        "restored": False,
    }
    save(state_path, state)
    save(Path("/run/layertrace-probe-measure-active.json"), {"state_file": str(state_path)})
    original_handlers = {}

    def stop(signum, _frame):
        raise KeyboardInterrupt(f"Signal {signum}")

    for signum in (signal.SIGTERM, signal.SIGINT):
        original_handlers[signum] = signal.signal(signum, stop)
    process = None
    samples = []
    report = None
    try:
        with (folder / "watchdog.log").open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "watchdog",
                    "--state",
                    str(state_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        ready = folder / "watchdog-ready.json"
        wait_deadline = time.monotonic() + 5
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= wait_deadline:
                raise RuntimeError("Measurement recovery process did not arm")
            time.sleep(0.05)
        with locked(state_path):
            network_setup(state)
            if mode == "off":
                for name in (PROBE, PROXY):
                    # Record recovery intent before mutation, including runner SIGKILL.
                    state["paused_by_measurement"].append(name)
                    save(state_path, state)
                    run(["docker", "pause", components[name]["container_id"]])
        started = time.monotonic()
        start_epoch = time.time()
        deadline = started + seconds
        state["deadline_monotonic"] = deadline + 30
        save(state_path, state)
        before = {name: counters(value) for name, value in components.items()}
        memory_before = machine_memory()
        while True:
            stamp = time.monotonic()
            row = {
                "elapsed_seconds": stamp - started,
                "at": datetime.now(UTC).isoformat(),
                "components": {name: counters(value) for name, value in components.items()},
                "probe_state": disk_usage(probe_state),
                "network": network_read(state),
            }
            samples.append(row)
            with (folder / "samples.jsonl").open("a") as log:
                log.write(json.dumps(row, ensure_ascii=False) + "\n")
            if stamp >= deadline:
                break
            time.sleep(min(interval, deadline - stamp))
        elapsed = time.monotonic() - started
        end_epoch = time.time()
        after = samples[-1]["components"]
        resources = {}
        for name in components:
            resources[name] = {
                "average_cpu_one_core_percent": (
                    after[name]["cpu_usage_usec"] - before[name]["cpu_usage_usec"]
                )
                / elapsed
                / 10000,
                "rss_mean_bytes": sum(s["components"][name]["rss_bytes"] for s in samples)
                / len(samples),
                "rss_peak_bytes": max(s["components"][name]["rss_bytes"] for s in samples),
                "cgroup_memory_peak_bytes": max(
                    s["components"][name]["cgroup_memory_current_bytes"] for s in samples
                ),
                "cgroup_write_bytes": after[name]["cgroup_write_bytes"]
                - before[name]["cgroup_write_bytes"],
                "process_write_bytes": after[name]["process_write_bytes"]
                - before[name]["process_write_bytes"],
                "process_wchar": after[name]["process_wchar"] - before[name]["process_wchar"],
            }
        final_business = business_identity()
        if business != final_business:
            raise RuntimeError("Business identity changed; measurement is not a comparable window")
        final_client = inspect(client_ids[0])
        if identity(client) != identity(final_client) or (results.parent / "pause").exists():
            raise RuntimeError("Client identity or load state changed during measurement")
        for name, saved in components.items():
            inspected = inspect(saved["container_id"])
            if inspected["Name"].lstrip("/") != name:
                raise RuntimeError("Probe component name changed during measurement")
            current = identity(inspected)
            expected = {key: value for key, value in saved.items() if key != "cgroup"}
            expected["paused"] = mode == "off"
            if current != expected:
                raise RuntimeError("Probe identity or pause state changed during measurement")
        report = {
            "run_id": run_id,
            "mode": mode,
            "elapsed_seconds": elapsed,
            "started_at": datetime.fromtimestamp(start_epoch, UTC).isoformat(),
            "ended_at": datetime.fromtimestamp(end_epoch, UTC).isoformat(),
            "resources": resources,
            "cpu_total_one_core_percent": sum(
                v["average_cpu_one_core_percent"] for v in resources.values()
            ),
            "probe_state_peak_logical_bytes": max(
                s["probe_state"]["logical_bytes"] for s in samples
            ),
            "probe_state_peak_allocated_bytes": max(
                s["probe_state"]["allocated_bytes"] for s in samples
            ),
            "network": samples[-1]["network"],
            "network_definition": "IP bytes at guest OUTPUT, matching only probe cgroup: loopback TCP destination 18000 for API upload; loopback source 9108 for metrics responses. Includes IP/TCP headers, ACK and retransmissions; excludes other workload cgroups and encrypted SSH envelope.",
            "measurement_scope": "CPU/RSS cover only the probe and read-only Docker gateway cgroups. Docker stats collection work in dockerd/containerd is excluded, so these figures are not the total overhead of the observability system. Business-side event emitters stay enabled in both modes. Successful task duration_ms is Agent processing time, including phase calls and event enqueue, rather than a client round-trip measurement.",
            "resource_accounting_scope": {
                "cpu_rss_included_components": [PROBE, PROXY],
                "cpu_rss_excluded_components": ["dockerd", "containerd"],
                "total_observability_system_overhead_measured": False,
                "note": "Reported CPU/RSS exclude daemon work induced by Docker stats requests, business-side event emitters, the control plane, Prometheus and the separate sampler. Task latency can reflect indirect effects of the sequential on/off windows but does not attribute their resource cost.",
            },
            "business_before": business,
            "business_after": final_business,
            "business": task_stats(results, start_epoch, end_epoch),
            "spool": {
                "before": spool_before,
                "window_end": spool_snapshot(probe_state),
                "configured_max_seconds": int(
                    next(
                        (
                            raw.split("=", 1)[1]
                            for raw in inspect(components[PROBE]["container_id"])["Config"]["Env"]
                            if raw.startswith("PROBE_SPOOL_MAX_SECONDS=")
                        ),
                        "600",
                    )
                ),
                "note": "Read-only aggregate snapshots outside the resource sampling interval. The recovery snapshot is immediate after unpause and does not prove later queue drainage. A paused probe cannot capture new telemetry; pause longer than its retention may expire previously queued events on resume. This A/B window is not a loss-free disconnect test.",
            },
            "sampler": {
                "pid": os.getpid(),
                "interval_seconds": interval,
                "note": "Same separate sampling process and counter setup in both modes; sampler overhead is excluded from probe component CPU counters.",
            },
            "environment": {
                "kernel": os.uname().release,
                "cpu_count": os.cpu_count(),
                "cpu_model": cpu_model(),
                "memory_before": memory_before,
                "memory_after": machine_memory(),
                "model_command": inspect("layertrace-demo-model")["Config"]["Cmd"],
                "model_cpu_limit_nanocpus": inspect("layertrace-demo-model")["HostConfig"][
                    "NanoCpus"
                ],
                "client_command": client["Config"]["Cmd"],
                "client_instance": identity(client),
                "client_settings": {
                    key: value
                    for raw in client["Config"]["Env"]
                    for key, value in [raw.split("=", 1)]
                    if key in {"LOAD_INTERVAL_SECONDS", "CLIENT_TIMEOUT_SECONDS"}
                },
            },
        }
    finally:
        try:
            restore(state_path)
        finally:
            if (
                process is not None
                and process.poll() is None
                and json.loads(state_path.read_text()).get("restored")
            ):
                process.terminate()
                process.wait(timeout=5)
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
    recovered = json.loads(state_path.read_text())
    if not recovered.get("restored"):
        raise RuntimeError("Measurement recovery was not verified; no success report was saved")
    report["restored"] = True
    report["restored_at"] = recovered["restored_at"]
    report["spool"]["immediately_after_restore"] = spool_snapshot(probe_state)
    report["spool"]["dropped_delta_immediately_after_restore"] = (
        report["spool"]["immediately_after_restore"]["dropped_count"]
        - spool_before["dropped_count"]
    )
    report["spool"]["gaps_changed_immediately_after_restore"] = (
        report["spool"]["immediately_after_restore"]["gaps"] != spool_before["gaps"]
    )
    save(folder / "report.json", report)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "mode": mode,
                "elapsed_seconds": elapsed,
                "cpu_total_one_core_percent": report["cpu_total_one_core_percent"],
                "business_successes": report["business"]["succeeded_count"],
                "restored": True,
                "report": str(folder / "report.json"),
            }
        ),
        flush=True,
    )


def measure(mode: str, seconds: int, interval: int, output: Path):
    if os.geteuid() != 0:
        raise RuntimeError("Use sudo for probe measurement")
    with Path("/run/layertrace-probe-measure.lock").open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another probe measurement is already running") from None
        try:
            active = Path("/run/layertrace-probe-measure-active.json")
            if active.exists():
                previous = Path(json.loads(active.read_text())["state_file"])
                if not previous.is_file() or not json.loads(previous.read_text()).get("restored"):
                    raise RuntimeError("Previous measurement recovery must finish before sampling")
            measure_window(mode, seconds, interval, output)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    sampling = sub.add_parser("sample")
    sampling.add_argument("--mode", choices=["on", "off"], required=True)
    sampling.add_argument("--seconds", type=int, default=900)
    sampling.add_argument("--interval", type=int, default=5)
    sampling.add_argument("--output", type=Path, default=Path("evidence/overhead"))
    for action in ("restore", "watchdog"):
        recovery = sub.add_parser(action)
        recovery.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "sample":
        measure(args.mode, args.seconds, args.interval, args.output)
    else:
        watchdog(args.state) if args.action == "watchdog" else restore(args.state)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from None
