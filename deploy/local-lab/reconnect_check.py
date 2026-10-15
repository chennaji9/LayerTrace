#!/usr/bin/env python3
"""Verify a real probe disconnect/restart without replacing business containers.

The preflight action is read-only. Run only in an isolated, authorized lab
window after real faults and overhead measurements have finished.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
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

PROBE = "layertrace-vm-probe"
PROXY = "layertrace-docker-proxy"
BUSINESS = ("layertrace-demo-model", "layertrace-demo-agent", "layertrace-demo-tool")

# This helper runs under sudo inside the guest. It returns only selected facts;
# neither docker Env nor the probe credential is printed or transferred.
GUEST = r"""
import fcntl,hashlib,json,os,pathlib,subprocess,sys,time,urllib.parse,urllib.request
from datetime import datetime,timezone
P=pathlib.Path
PROBE="layertrace-vm-probe"
PROXY="layertrace-docker-proxy"
BUSINESS=("layertrace-demo-model","layertrace-demo-agent","layertrace-demo-tool")
def call(args):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=35)
    if p.returncode: raise RuntimeError("Guest operation failed: "+args[0]+" "+args[1])
    return p.stdout
def inspect(target):
    return json.loads(call(["docker","inspect",target]))[0]
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
def safe(value):
    return {"container_id":value["Id"],"started_at":value["State"]["StartedAt"],
        "pid":value["State"]["Pid"],"running":value["State"]["Running"],
        "paused":value["State"]["Paused"],"config":{k:value["HostConfig"].get(k)
        for k in ("NanoCpus","CpuQuota","CpuPeriod","Memory","MemorySwap","RestartPolicy")}}
def target(cid):
    v=inspect(cid)
    if v["Id"]!=cid or v["Name"].lstrip("/")!=PROBE or "agents.vm_probe" not in v["Config"]["Cmd"]:
        raise RuntimeError("Saved probe identity does not match")
    return v
def volume(value):
    mounts=[P(m["Source"]) for m in value["Mounts"] if m["Destination"]=="/var/lib/layertrace"]
    if len(mounts)!=1 or not mounts[0].is_dir(): raise RuntimeError("Probe state volume is absent")
    return mounts[0]
def active_check():
    paths=list((P(request["source"])/"evidence").rglob("state.json"))
    marker=P("/run/layertrace-probe-measure-active.json")
    if marker.exists(): paths.append(P(json.loads(marker.read_text())["state_file"]))
    for path in set(paths):
        state=json.loads(path.read_text())
        if state.get("mode") in ("process-failure","cpu-quota","tool-503") and state.get("state")!="restored":
            raise RuntimeError("Unrestored real fault prevents reconnect verification")
        if state.get("mode") in ("on","off") and not state.get("restored"):
            raise RuntimeError("Unrestored overhead window prevents reconnect verification")
def queue(value,save_raw=None):
    import sqlite3
    root=volume(value)
    uri="file:"+urllib.parse.quote(str(root/"outbox.sqlite3"),safe="/")+"?mode=ro"
    db=sqlite3.connect(uri,uri=True,timeout=5)
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")
    rows=db.execute("SELECT event_id,payload,enqueued_at FROM outbox ORDER BY enqueued_at,event_id").fetchall()
    meta={k:json.loads(v) for k,v in db.execute("SELECT key,value FROM meta WHERE key IN ('dropped_count','gaps','docker_since')")}
    db.close()
    events=[]
    raw=[]
    for eid,body,enqueued in rows:
        payload=json.loads(body)
        raw.append(payload)
        events.append({"event_id":eid,"event_type":payload["event_type"],"occurred_at":payload["occurred_at"],
            "observed_at":payload["observed_at"],"payload_sha256":hashlib.sha256(body.encode()).hexdigest(),
            "attributes_sha256":digest(payload.get("attributes",{})),"resource_ref":payload["resource_ref"],
            "source":payload["source"],"provenance":payload["provenance"],"enqueued_at":enqueued,
            "task_id":(payload.get("correlation") or {}).get("task_id")})
    if save_raw is not None:
        file=P(save_raw)
        with file.open("w",encoding="utf-8") as out: json.dump(raw,out,ensure_ascii=False)
        file.chmod(0o600)
    credential=json.loads((root/"probe-credential.json").read_text())
    expiry=datetime.fromisoformat(credential["expires_at"].replace("Z","+00:00"))
    return {"depth":len(events),"events":events,"dropped_count":int(meta.get("dropped_count",0)),
        "gaps":meta.get("gaps",[]),"docker_since":meta.get("docker_since"),
        "probe_id":credential["probe_id"],"vm_id":credential["vm_id"],
        "credential_expires_at":credential["expires_at"],
        "credential_remaining_seconds":(expiry-datetime.now(timezone.utc)).total_seconds()}
def snapshot(check_active=False):
    if check_active: active_check()
    values=json.loads(call(["docker","inspect",PROBE,PROXY,*BUSINESS]))
    by_name={v["Name"].lstrip("/"):v for v in values}
    probe=by_name[PROBE]
    target(probe["Id"])
    env=dict(line.split("=",1) for line in probe["Config"]["Env"] if "=" in line)
    if env.get("PROBE_VM_EXTERNAL_ID")!=request["vm_uuid"]:
        raise RuntimeError("Probe is bound to another business VM")
    if env.get("LAYERTRACE_API")!="http://127.0.0.1:18000":
        raise RuntimeError("Reconnect check requires the pinned guest loopback tunnel")
    return {"at":datetime.now(timezone.utc).isoformat(),"components":{k:safe(v) for k,v in by_name.items()},
        "queue":queue(probe),"vm_uuid":request["vm_uuid"]}
def save(path,data):
    p=P(path);temporary=p.with_suffix(".tmp")
    temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2));temporary.chmod(0o600);temporary.replace(p)
def restore_probe(cid):
    v=target(cid)
    if v["State"]["Paused"]: raise RuntimeError("Probe was paused externally; no automatic unpause")
    if not v["State"]["Running"]: call(["docker","start",cid])
    return safe(target(cid))
action=request["action"]
if action=="snapshot":
    result=snapshot(request.get("check_active",False))
elif action=="capture":
    result=queue(target(request["probe_id"]),request.get("save_raw"))
elif action=="restart":
    active_check()
    v=target(request["probe_id"])
    if not v["State"]["Running"] or v["State"]["Paused"]: raise RuntimeError("Original probe is not running")
    call(["docker","restart","--time","20",request["probe_id"]])
    result=safe(target(request["probe_id"]))
elif action=="reserve":
    root=P(request["guard_directory"]);root.mkdir(mode=0o700,parents=True)
    if root.is_symlink(): raise RuntimeError("Guard directory must not be a symlink")
    script=root/"guard.py";script.write_text(request["guard_code"]);script.chmod(0o700)
    save(root/"lease.json",{"restored":False,"released":False,"deadline_monotonic":time.monotonic()+request["lease_seconds"]})
    log=root/"guard.log"
    with log.open("ab") as out:
        log.chmod(0o600)
        process=subprocess.Popen([sys.executable,str(script)],stdin=subprocess.DEVNULL,stdout=out,stderr=out,start_new_session=True,close_fds=True)
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and not (root/"ready.json").exists() and process.poll() is None: time.sleep(.1)
    if not (root/"ready.json").exists(): raise RuntimeError("Guest reservation did not become ready")
    result=json.loads((root/"ready.json").read_text())
elif action=="guard":
    root=P(request["guard_directory"])
    try:
        deadline_recovery=False
        with P("/run/layertrace-probe-measure.lock").open("a+") as lock:
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            active_check()
            save(root/"ready.json",{"pid":os.getpid(),"at":datetime.now(timezone.utc).isoformat()})
            while True:
                lease=json.loads((root/"lease.json").read_text())
                if lease.get("released"): break
                remaining=lease["deadline_monotonic"]-time.monotonic()
                if remaining<=0:
                    restore_probe(request["probe_id"])
                    deadline_recovery=True
                    break
                time.sleep(min(.5,remaining))
        save(root/"guard-result.json",{"reservation_released":True,"deadline_recovery":deadline_recovery,
            "completed_at":datetime.now(timezone.utc).isoformat()})
        result={"guard_complete":True}
    except Exception as exc:
        save(root/"guard-result.json",{"error_type":type(exc).__name__})
        raise SystemExit(1)
elif action=="restore":
    result=restore_probe(request["probe_id"])
    root=P(request["guard_directory"])
    if (root/"lease.json").exists():
        lease=json.loads((root/"lease.json").read_text())
        lease["restored"]=True;save(root/"lease.json",lease)
elif action=="release":
    root=P(request["guard_directory"])
    if (root/"lease.json").exists():
        lease=json.loads((root/"lease.json").read_text())
        if not lease.get("restored"): raise RuntimeError("Guest reservation cannot release before recovery")
        lease["released"]=True;save(root/"lease.json",lease)
        deadline=time.monotonic()+5
        marker=root/"guard-result.json"
        while not marker.exists() and time.monotonic()<deadline: time.sleep(.1)
        if not marker.exists() or not json.loads(marker.read_text()).get("reservation_released"):
            raise RuntimeError("Guest sampling lock release was not verified")
    result={"reservation_released":True}
elif action=="replay":
    probe=target(request["probe_id"])
    root=volume(probe)
    credential=json.loads((root/"probe-credential.json").read_text())
    events=json.loads(P(request["raw_file"]).read_text())
    results=[]
    for offset in range(0,len(events),200):
        chunk=events[offset:offset+200]
        body=json.dumps({"schema_version":"1.0","events":chunk},ensure_ascii=False).encode()
        url="http://127.0.0.1:18000/api/v1/probes/"+urllib.parse.quote(credential["probe_id"],safe="")+"/events"
        req=urllib.request.Request(url,data=body,method="POST",headers={"Authorization":"Bearer "+credential["credential"],"Content-Type":"application/json"})
        with urllib.request.urlopen(req,timeout=25) as response:
            response_body=json.load(response)
            results.append({"http_status":response.status,"count":len(chunk),**{key:int(response_body.get(key,0)) for key in ("accepted","duplicate","rejected","unresolved")}})
    result={"count":len(events),"batches":results}
else:
    raise RuntimeError("Unknown guest operation")
print(json.dumps(result,ensure_ascii=False))
"""

SQL = r"""
import asyncio,hashlib,json,os,sys,uuid
from pathlib import Path
import asyncpg
from apps.control_plane.config import Settings
request=json.load(sys.stdin)
for raw in Path(request["env_file"]).read_text().splitlines():
    line=raw.strip()
    if line and not line.startswith("#") and "=" in line:
        key,value=line.split("=",1);os.environ[key.strip()]=value.strip().strip("\"'")
settings=Settings(_env_file=None)
def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()
async def main():
    connection=await asyncpg.connect(settings.database_url.replace("postgresql+asyncpg:","postgresql:"),timeout=10)
    try:
        async with connection.transaction(readonly=True,isolation="repeatable_read"):
            rows=await connection.fetch("SELECT id,event_id,type,occurred_at,observed_at,payload,resource_ref,source,provenance,correlation FROM events WHERE event_id=ANY($1::text[]) AND project_id=$2::uuid ORDER BY event_id",request["event_ids"],uuid.UUID(settings.project_id))
            events=[]
            for row in rows:
                attributes=json.loads(row["payload"]) if isinstance(row["payload"],str) else row["payload"]
                ref=json.loads(row["resource_ref"]) if isinstance(row["resource_ref"],str) else row["resource_ref"]
                correlation=json.loads(row["correlation"]) if isinstance(row["correlation"],str) else row["correlation"]
                events.append({"id":str(row["id"]),"event_id":row["event_id"],"event_type":row["type"],"occurred_at":row["occurred_at"].isoformat(),"observed_at":row["observed_at"].isoformat(),"attributes_sha256":digest(attributes),"resource_ref":ref,"source":row["source"],"provenance":row["provenance"],"task_id":(correlation or {}).get("task_id")})
            print(json.dumps({"events":events,"event_count":len(events),"stale_after_seconds":settings.heartbeat_interval_seconds*settings.stale_after_missed_heartbeats}))
    finally: await connection.close()
asyncio.run(main())
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def runtime(config: Path):
    spec = importlib.util.spec_from_file_location(
        "layertrace_local_runtime", Path(__file__).with_name("runtime.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.Runtime(config)


def guest(lab, action: str, **facts) -> dict:
    request = {
        "action": action,
        "source": lab.cfg["vm_source_directory"],
        "vm_uuid": lab.vm,
        **facts,
    }
    code = "import json\nrequest=json.loads(" + repr(json.dumps(request)) + ")\n" + GUEST
    return json.loads(lab.remote("vm", "sudo python3 -", code))


def api(module, lab, path: str) -> dict:
    settings = module.read_env(lab.path("control_plane_env"))
    status, body, _ = lab.request(
        "GET",
        lab.cfg["api_url"] + path,
        headers={"Authorization": "Bearer " + settings["API_TOKENS"].split(",")[0]},
    )
    if status != 200:
        raise RuntimeError("Public API lookup failed")
    return body


def source_summary(module, lab) -> dict:
    body = api(module, lab, "/api/v1/status")
    return {
        "generated_at": body["generated_at"],
        "sources": [
            {
                key: row.get(key)
                for key in (
                    "type",
                    "instance",
                    "state",
                    "last_success_at",
                    "capabilities",
                    "dropped_count",
                    "data_gaps",
                )
            }
            for row in body["sources"]
        ],
    }


def healthy(status: dict) -> bool:
    rows = status["sources"]
    return (
        len(rows) == 4
        and {r["type"] for r in rows} == {"probe", "zsvirt", "postgres", "prometheus"}
        and all(r["state"] == "healthy" for r in rows)
    )


def sql(lab, event_ids: list[str]) -> dict:
    result = subprocess.run(
        [str(lab.root / ".venv/bin/python"), "-c", SQL],
        cwd=lab.root,
        input=json.dumps({"env_file": str(lab.path("control_plane_env")), "event_ids": event_ids}),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise RuntimeError(
            "Read-only Event database lookup failed; no credential diagnostics emitted"
        )
    return json.loads(result.stdout)


def save(path: Path, value: dict):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


@contextmanager
def locked(path: Path, *, nonblocking: bool = False):
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0))
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def update(path: Path, **facts) -> dict:
    with locked(path.with_suffix(".lock")):
        value = json.loads(path.read_text())
        value.update(facts)
        save(path, value)
        return value


def restore(path: Path) -> dict:
    with locked(path.with_suffix(".lock")):
        state = json.loads(path.read_text())
        module, lab = runtime(Path(state["config"]))
        with module.exclusive(lab.runtime / "runtime.lock"):
            lab.ensure_tunnel()
        tunnel_restored = time.monotonic()
        probe = guest(
            lab,
            "restore",
            probe_id=state["probe_container_id"],
            guard_directory=state["guard_directory"],
        )
        if not probe["running"] or probe["paused"]:
            raise RuntimeError("Original probe recovery was not verified")
        state.update(restored=True, restored_at=now(), recovery_probe=probe)
        state.setdefault("tunnel_restored_monotonic", tunnel_restored)
        save(path, state)
        return state


def watchdog(path: Path):
    save(path.with_name("watchdog-ready.json"), {"pid": os.getpid(), "ready_at": now()})
    while True:
        state = json.loads(path.read_text())
        if state.get("restored"):
            return
        remaining = state["deadline_monotonic"] - time.monotonic()
        if remaining <= 0:
            try:
                recovered = restore(path)
                save(
                    path.with_name("watchdog-result.json"),
                    {"restored": recovered["restored"], "at": now()},
                )
            except Exception as exc:
                save(
                    path.with_name("watchdog-result.json"),
                    {"restored": False, "error_type": type(exc).__name__, "at": now()},
                )
            return
        time.sleep(min(0.5, remaining))


def preflight(config: Path, seconds: int) -> dict:
    module, lab = runtime(config)
    snapshot = guest(lab, "snapshot", check_active=True)
    status = source_summary(module, lab)
    settings = sql(lab, [])
    if not healthy(status):
        raise RuntimeError("All four sources must be healthy before this independent window")
    if any(not v["running"] or v["paused"] for v in snapshot["components"].values()):
        raise RuntimeError(
            "Probe, gateway and all business containers must be running and unpaused"
        )
    if snapshot["queue"]["depth"]:
        raise RuntimeError("Existing outbox must be empty before disconnect verification")
    if snapshot["queue"]["credential_remaining_seconds"] < seconds + 120:
        raise RuntimeError("Wait for normal probe credential renewal before the disconnect window")
    if seconds <= settings["stale_after_seconds"]:
        raise RuntimeError("Disconnect window must exceed the current stale threshold")
    if not lab.owned_pid("tunnel", lab.tunnel_signature()):
        raise RuntimeError("The signed collection tunnel must be running before the window")
    return {
        "at": now(),
        "snapshot": snapshot,
        "status": status,
        "stale_after_seconds": settings["stale_after_seconds"],
    }


def unchanged(before: dict, after: dict) -> bool:
    fields = ("container_id", "started_at", "running", "paused", "config")
    return all(
        {k: before[name][k] for k in fields} == {k: after[name][k] for k in fields}
        for name in (*BUSINESS, PROXY)
    )


def check_sql(expected: list[dict], actual: dict):
    rows = actual["events"]
    by_id = {r["event_id"]: r for r in rows}
    if len(rows) != len(expected) or len(by_id) != len(expected):
        raise RuntimeError("Captured events were not stored exactly once")
    for event in expected:
        stored = by_id[event["event_id"]]
        for key in ("occurred_at", "observed_at"):
            if datetime.fromisoformat(event[key].replace("Z", "+00:00")) != datetime.fromisoformat(
                stored[key].replace("Z", "+00:00")
            ):
                raise RuntimeError("Original event timestamp changed during upload")
        for key in ("event_type", "attributes_sha256", "resource_ref", "source", "provenance"):
            if event[key] != stored[key]:
                raise RuntimeError("Stored event fact does not match the committed outbox")


def run(config: Path, seconds: int, output: Path):
    if os.geteuid() != 0:
        raise RuntimeError("Run this local lab check using sudo")
    module, lab = runtime(config)
    with locked(lab.runtime / "probe-reconnect.lock", nonblocking=True):
        previous = lab.runtime / "probe-reconnect-active.json"
        if previous.exists():
            old = Path(json.loads(previous.read_text())["state_file"])
            if not old.is_file() or not json.loads(old.read_text()).get("restored"):
                raise RuntimeError("Prior reconnect recovery must finish first")
        before = preflight(config, seconds)
        run_id = "probe-reconnect-" + uuid.uuid4().hex[:12]
        folder = (output / run_id).resolve()
        folder.mkdir(parents=True, mode=0o700)
        state_path = folder / "state.json"
        cid = before["snapshot"]["components"][PROBE]["container_id"]
        guard_dir = "/run/layertrace-probe-reconnect/" + run_id
        state = {
            "run_id": run_id,
            "config": str(config.resolve()),
            "requested_disconnect_seconds": seconds,
            "probe_container_id": cid,
            "guard_directory": guard_dir,
            "restored": False,
            "deadline_monotonic": time.monotonic() + seconds + 90,
            "phase": "prepared",
            "before": before,
        }
        save(state_path, state)
        save(previous, {"state_file": str(state_path)})
        guard_request = {
            "action": "guard",
            "source": lab.cfg["vm_source_directory"],
            "vm_uuid": lab.vm,
            "probe_id": cid,
            "guard_directory": guard_dir,
        }
        guard_code = (
            "import json\nrequest=json.loads(" + repr(json.dumps(guard_request)) + ")\n" + GUEST
        )
        process = None
        report = None
        original = {}

        def stop(signum, _frame):
            raise KeyboardInterrupt(f"Signal {signum}")

        for signum in (signal.SIGTERM, signal.SIGINT):
            original[signum] = signal.signal(signum, stop)
        try:
            reservation = guest(
                lab,
                "reserve",
                probe_id=cid,
                guard_directory=guard_dir,
                guard_code=guard_code,
                lease_seconds=seconds + 240,
            )
            save(folder / "guest-reservation.json", reservation)
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
            deadline = time.monotonic() + 5
            while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
                time.sleep(0.1)
            if not ready.exists():
                raise RuntimeError("Independent reconnect watchdog did not become ready")
            update(state_path, phase="disconnecting")
            with module.exclusive(lab.runtime / "runtime.lock"):
                lab.stop_tunnel()
            disconnected = time.monotonic()
            update(
                state_path,
                disconnected_at=now(),
                disconnected_monotonic=disconnected,
                deadline_monotonic=disconnected + seconds,
                phase="disconnected",
            )
            time.sleep(15)
            raw_file = guard_dir + "/committed-events.json"
            committed = guest(lab, "capture", probe_id=cid, save_raw=raw_file)
            save(folder / "committed-before-restart.json", committed)
            if not committed["depth"]:
                raise RuntimeError("No real committed probe events accumulated during disconnect")
            queued_before_upload = sql(lab, [r["event_id"] for r in committed["events"]])
            save(folder / "events-during-disconnect.json", queued_before_upload)
            if queued_before_upload["event_count"]:
                raise RuntimeError(
                    "Captured events already existed before reconnect; use a fresh window"
                )
            restart = guest(lab, "restart", probe_id=cid)
            save(folder / "probe-restart.json", {"at": now(), **restart})
            if (
                restart["container_id"] != cid
                or restart["started_at"] == before["snapshot"]["components"][PROBE]["started_at"]
            ):
                raise RuntimeError("Original probe process restart was not verified")
            recovered = guest(lab, "capture", probe_id=cid)
            save(folder / "committed-after-restart.json", recovered)
            old_events = {r["event_id"]: r["payload_sha256"] for r in committed["events"]}
            new_events = {r["event_id"]: r["payload_sha256"] for r in recovered["events"]}
            if any(new_events.get(key) != digest for key, digest in old_events.items()):
                raise RuntimeError(
                    "Committed outbox events changed or disappeared across probe restart"
                )
            if (
                recovered["dropped_count"] != committed["dropped_count"]
                or recovered["gaps"] != committed["gaps"]
            ):
                raise RuntimeError("Outbox drop/gap changed across restart")
            observe_at = disconnected + max(before["stale_after_seconds"] + 3, seconds - 10)
            while time.monotonic() < observe_at:
                time.sleep(max(0, min(0.5, observe_at - time.monotonic())))
            stale = source_summary(module, lab)
            save(folder / "status-disconnected.json", stale)
            workloads_stale = api(module, lab, "/api/v1/workloads?limit=200")
            save(folder / "workloads-disconnected.json", workloads_stale)
            if not any(
                row["type"] == "probe" and row["state"] == "stale" for row in stale["sources"]
            ):
                raise RuntimeError("Public API did not report the disconnected probe as stale")
            while time.monotonic() < disconnected + seconds:
                time.sleep(max(0, min(0.5, disconnected + seconds - time.monotonic())))
            restored = restore(state_path)
            elapsed = restored["tunnel_restored_monotonic"] - disconnected
            update(state_path, actual_disconnect_seconds=elapsed, phase="catching_up")
            deadline = time.monotonic() + 180
            final_queue = status = None
            while time.monotonic() < deadline:
                final_queue = guest(lab, "capture", probe_id=cid)
                status = source_summary(module, lab)
                if final_queue["depth"] == 0 and healthy(status):
                    break
                time.sleep(2)
            else:
                raise RuntimeError(
                    "Probe did not drain its queue and restore all four healthy sources"
                )
            save(folder / "queue-final.json", final_queue)
            save(folder / "status-restored.json", status)
            if (
                final_queue["dropped_count"] != before["snapshot"]["queue"]["dropped_count"]
                or final_queue["gaps"] != before["snapshot"]["queue"]["gaps"]
            ):
                raise RuntimeError("Dropped events or gaps increased during the window")
            event_ids = list(old_events)
            sql_before = sql(lab, event_ids)
            save(folder / "events-stored-before-replay.json", sql_before)
            check_sql(committed["events"], sql_before)
            replay = guest(lab, "replay", probe_id=cid, raw_file=raw_file)
            save(folder / "duplicate-replay.json", replay)
            if replay["count"] != len(event_ids) or any(
                r["http_status"] != 202
                or r["accepted"] != 0
                or r["duplicate"] != r["count"]
                or r["rejected"] != 0
                for r in replay["batches"]
            ):
                raise RuntimeError(
                    "Real probe endpoint did not acknowledge an idempotent duplicate replay"
                )
            sql_after = sql(lab, event_ids)
            save(folder / "events-stored-after-replay.json", sql_after)
            check_sql(committed["events"], sql_after)
            if sql_before["events"] != sql_after["events"]:
                raise RuntimeError("Duplicate replay changed Event storage")
            after = guest(lab, "snapshot")
            save(folder / "environment-after.json", after)
            if not unchanged(before["snapshot"]["components"], after["components"]):
                raise RuntimeError(
                    "Business or gateway identity/configuration changed during reconnect verification"
                )
            workloads = api(module, lab, "/api/v1/workloads?limit=200")
            save(folder / "workloads-restored.json", workloads)
            seen = {row["container_id"]: row for row in workloads["items"]}
            if any(
                seen.get(after["components"][name]["container_id"], {}).get("data_state")
                != "current"
                for name in BUSINESS
            ):
                raise RuntimeError(
                    "Public workload API did not return current business metrics after recovery"
                )
            for task_id in sorted(
                {row["task_id"] for row in committed["events"] if row.get("task_id")}
            ):
                import urllib.parse

                save(
                    folder / ("task-" + uuid.uuid5(uuid.NAMESPACE_URL, task_id).hex + ".json"),
                    api(module, lab, "/api/v1/tasks/" + urllib.parse.quote(task_id, safe="")),
                )
            report = {
                "run_id": run_id,
                "requested_disconnect_seconds": seconds,
                "actual_disconnect_seconds": elapsed,
                "stale_threshold_seconds": before["stale_after_seconds"],
                "stale_observed": True,
                "probe_same_container_new_process": True,
                "committed_events_retained": len(event_ids),
                "captured_event_count_before_reconnect": 0,
                "original_timestamps_preserved": True,
                "events_stored_once": True,
                "duplicate_endpoint_http202": True,
                "duplicate_count": len(event_ids),
                "queue_final": final_queue["depth"],
                "four_sources_healthy": True,
                "dropped_count_delta": 0,
                "gaps_unchanged": True,
                "business_gateway_unchanged": True,
                "restored": True,
                "completed_at": now(),
            }
        except BaseException as exc:
            update(state_path, phase="failed", error_type=type(exc).__name__)
            raise
        finally:
            restoration_error = None
            try:
                restore(state_path)
                guest(lab, "release", guard_directory=guard_dir)
            except Exception as exc:
                restoration_error = type(exc).__name__
                update(state_path, restoration_error_type=type(exc).__name__)
            if (
                process
                and process.poll() is None
                and json.loads(state_path.read_text()).get("restored")
            ):
                process.terminate()
                process.wait(timeout=5)
            for signum, handler in original.items():
                signal.signal(signum, handler)
            if restoration_error and report is not None:
                update(state_path, phase="failed", validation_complete=False)
                raise RuntimeError("Final recovery or sampling lock release remains incomplete")
        report["reservation_released"] = True
        save(folder / "report.json", report)
        update(state_path, phase="complete", validation_complete=True)
        print(
            json.dumps({**report, "evidence_directory": str(folder)}, ensure_ascii=False),
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "run", "restore", "watchdog"))
    parser.add_argument(
        "--config", type=Path, default=Path("/mnt/d/ZSvirt/layertrace-runtime/runtime.json")
    )
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/mnt/d/ZSvirt/layertrace-runtime/evidence/probe-reconnect"),
    )
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if not 60 <= args.seconds <= 180:
        parser.error("Disconnect duration must be 60..180 seconds")
    if args.action in ("watchdog", "restore"):
        if not args.state:
            parser.error("This action requires --state")
        if args.action == "watchdog":
            watchdog(args.state)
        else:
            restored = restore(args.state)
            _module, lab = runtime(Path(restored["config"]))
            released = guest(lab, "release", guard_directory=restored["guard_directory"])
            print(
                json.dumps(
                    {"restored": restored["restored"], **released, "state_file": str(args.state)}
                )
            )
    elif args.action == "preflight":
        print(json.dumps(preflight(args.config, args.seconds), ensure_ascii=False))
    else:
        run(args.config, args.seconds, args.output)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {"phase": "failed", "error_type": type(error).__name__, "message": str(error)}
            ),
            flush=True,
        )
        raise SystemExit(1) from None
