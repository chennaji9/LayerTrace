#!/usr/bin/env python3
"""Cold-start the built artifact in disposable Docker resources and record evidence.

This test submits synthetic facts through the production API. It does not read
runtime secrets, contact ZSvirt, or touch a business VM. Run with project Python:
  .venv/bin/python scripts/cold_smoke.py --directory /absolute/new/evidence-dir
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

PG_IMAGE = "postgres@sha256:5660c2cbfea50c7a9127d17dc4e48543eedd3d7a41a595a2dfa572471e37e64c"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--image", default="layertrace-control-plane:local")
    parser.add_argument("--port", default=18080, type=int)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    run_id = secrets.token_hex(5)
    prefix = f"layertrace-cold-{run_id}"
    network = prefix + "-net"
    names = {key: prefix + "-" + key for key in ("db", "api", "worker", "prom")}
    api_token, registration_secret, db_password = (secrets.token_urlsafe(32) for _ in range(3))
    hidden = [api_token, registration_secret, db_password]
    report = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "scope": "Isolated built-artifact smoke using synthetic API inputs; no ZSvirt or business VM access",
        "directory_initially_empty": True,
        "checks": [],
        "commands": [],
        "image_tag": args.image,
        "postgres_image": PG_IMAGE,
        "resources": names,
        "success": False,
    }

    def scrub(value: str) -> str:
        for secret in hidden:
            value = value.replace(secret, "[REDACTED]")
        return value

    def command(argv: list[str], *, required=True) -> str:
        report["commands"].append(argv)
        result = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=120)
        output = scrub(result.stdout + result.stderr)
        if required and result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {argv[0:2]}: {output}")
        return output.strip()

    def check(name: str, condition: bool, detail=None) -> None:
        report["checks"].append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(name)

    def wait_for(predicate, *, timeout=35):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            time.sleep(0.5)
        raise TimeoutError("Smoke state did not become ready")

    try:
        report["image_id"] = command(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"])
        command(["docker", "network", "create", "--label", f"layertrace.cold-smoke={run_id}", network])
        gateway = command(["docker", "network", "inspect", network, "--format", "{{(index .IPAM.Config 0).Gateway}}"])
        db_env = directory / "db.env"
        db_env.write_text(f"POSTGRES_USER=smoke\nPOSTGRES_DB=smoke\nPOSTGRES_PASSWORD={db_password}\n")
        db_env.chmod(0o600)
        command(["docker", "run", "-d", "--name", names["db"], "--network", network,
                 "--label", f"layertrace.cold-smoke={run_id}", "--env-file", str(db_env), PG_IMAGE])

        def db_ready():
            return subprocess.run(["docker", "exec", names["db"], "pg_isready", "-h", "127.0.0.1", "-U", "smoke", "-d", "smoke"],
                                  capture_output=True, check=False, timeout=10).returncode == 0

        wait_for(db_ready)
        report["postgres_version"] = command(["docker", "exec", names["db"], "psql", "-U", "smoke", "-d", "smoke",
                                              "-Atc", "SELECT version()"])
        check("postgres_16_4", report["postgres_version"].startswith("PostgreSQL 16.4 "))
        env = directory / "runtime.env"
        env.write_text("\n".join([
            f"DATABASE_URL=postgresql+asyncpg://smoke:{db_password}@{names['db']}:5432/smoke",
            f"API_TOKENS={api_token}", f"PROBE_REGISTRATION_SECRET={registration_secret}",
            "MODE=prod", "LAB_ENABLED=false", "ZSVIRT_ENABLED=false", "REQUIRE_TLS_OFF_LOOPBACK=true",
            f"PLAINTEXT_CLIENT_CIDRS={gateway}/32", "TRUSTED_PROXIES=127.0.0.1/32,::1/128",
            f"PROMETHEUS_URL=http://{names['prom']}:9090", "INCIDENT_RECOVERY_STABLE_SECONDS=60",
        ]) + "\n")
        env.chmod(0o600)
        common = ["--network", network, "--env-file", str(env), "--label", f"layertrace.cold-smoke={run_id}"]
        migration = command(["docker", "run", "--rm", *common, args.image, "layertrace", "migrate"])
        (directory / "migration.log").write_text(migration + "\n")
        revision = command(["docker", "exec", names["db"], "psql", "-U", "smoke", "-d", "smoke",
                            "-Atc", "SELECT version_num FROM alembic_version"])
        check("fresh_migration", revision == "0002", revision)
        command(["docker", "run", "-d", "--name", names["api"], *common,
                 "-p", f"127.0.0.1:{args.port}:8000", args.image])
        report["runtime_version"] = command(["docker", "exec", names["api"], "python", "-c",
                                            "import sys,importlib.metadata as m; print(sys.version); print('layertrace='+m.version('layertrace')); print('fastapi='+m.version('fastapi')); print('asyncpg='+m.version('asyncpg'))"])
        base = f"http://127.0.0.1:{args.port}"
        with httpx.Client(base_url=base, timeout=10, follow_redirects=False) as client:
            def api_ready():
                try:
                    return client.get("/healthz").status_code == 200
                except httpx.HTTPError:
                    return False

            wait_for(api_ready)
            user = {"Authorization": f"Bearer {api_token}"}
            registration = {"Authorization": f"Bearer {registration_secret}"}
            check("api_missing_token_401", client.get("/api/v1/resources").status_code == 401)
            check("api_invalid_token_401", client.get("/api/v1/resources", headers={"Authorization": "Bearer invalid"}).status_code == 401)
            empty = client.get("/api/v1/resources", headers=user)
            check("empty_database_authenticated", empty.status_code == 200 and empty.json()["items"] == [])
            client.get("/login")
            nonce = client.cookies.get("layertrace_login_nonce")
            check("login_nonce_required", client.post("/login", data={"token": api_token, "login_nonce": "invalid"}).status_code == 403)
            check("login_bad_token_401", client.post("/login", data={"token": "invalid", "login_nonce": nonce}).status_code == 401)
            login = client.post("/login", data={"token": api_token, "login_nonce": nonce, "next": "/"})
            cookie = client.cookies.get("layertrace_session", "")
            payload = cookie.split(".")[0]
            decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode()
            session_header = next(value for value in login.headers.get_list("set-cookie") if value.startswith("layertrace_session="))
            check("browser_signed_session", login.status_code == 303 and api_token not in cookie + decoded and "HttpOnly" in session_header)
            check("browser_read_access", client.get("/api/v1/resources").status_code == 200)
            check("production_lab_disabled", client.post("/api/v1/scenarios/story-oom:run", headers=user, json={"mode": "lab"}).status_code == 403)
            probe_body = {"installation_id": "cold-smoke", "vm_ref": {"namespace": "zsvirt.vm", "external_id": "cold-vm"}, "profile": "lightweight"}
            check("probe_registration_requires_secret", client.post("/api/v1/probes:register", json=probe_body).status_code == 403)
            registered = client.post("/api/v1/probes:register", headers=registration, json=probe_body)
            check("probe_register", registered.status_code == 201)
            probe = registered.json()
            hidden.append(probe["credential"])
            collector = {"Authorization": f"Bearer {probe['credential']}"}
            probe_id = probe["probe_id"]
            now = datetime.now(UTC)
            started = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S") + ".430786695Z"
            cid = "a" * 20 + "13812345678" + "b" * 33
            siid = cid + "@" + started
            identity = {key: cid if key == "container_id" else siid for key in
                        ("container_id", "instance_id", "service_instance_id", "model_instance_id", "tool_instance_id")}
            inventory = {"observed_at": now.isoformat(), "containers": [{"container_id": cid, "name": "cold-agent", "service_name": "cold-agent",
                         "instance_id": siid, "started_at": started, "state": "running", "limits": {"memory_limit_bytes": 536870912}}]}
            check("probe_inventory", client.post(f"/api/v1/probes/{probe_id}:inventory", headers=collector, json=inventory).status_code == 200)
            resources = client.get("/api/v1/resources", headers=user).json()["items"]
            check("full_resource_identity", any(r["source_id"] == cid for r in resources) and any(r["source_id"] == siid for r in resources), {"container_id": cid, "instance_id": siid})

            def heartbeat():
                return client.post(f"/api/v1/probes/{probe_id}:heartbeat", headers=collector,
                                   json={"observed_at": datetime.now(UTC).isoformat(), "queue_depth": 0, "dropped_count": 0})

            check("authenticated_heartbeat", heartbeat().status_code == 204)

            def event(kind, offset, namespace, external_id, attributes, task=False):
                at = (now - timedelta(seconds=10) + timedelta(seconds=offset)).isoformat()
                value = {"schema_version": "1.0", "event_id": "cold-" + kind, "event_type": kind,
                         "resource_ref": {"namespace": namespace, "external_id": external_id}, "occurred_at": at, "observed_at": at,
                         "severity": "info" if kind == "task.started" else "error", "source": "cold-smoke-input", "provenance": "observed", "attributes": attributes}
                if task:
                    value["correlation"] = {"task_id": "cold-task"}
                return value

            facts = [event("task.started", 0, "otel.service_instance", siid, identity, True),
                     event("container.exit", 1, "docker.container", cid, {"exit_code": 137, "signal": "SIGKILL"}),
                     event("task.failed", 2, "otel.service_instance", siid, {**identity, "observer": "client", "duration_ms": 2000, "message": "call 13812345678"}, True)]
            batch = {"schema_version": "1.0", "events": facts}
            ingested = client.post("/api/v1/events:batch", headers=collector, json=batch)
            check("facts_ingested_and_resolved", ingested.status_code == 202 and ingested.json()["accepted"] == 3 and ingested.json()["unresolved"] == 0, ingested.json())
            check("web_does_not_project", client.get("/api/v1/tasks?task_id=cold-task", headers=user).json()["items"] == [])
            stub = directory / "prometheus_stub.py"
            stub.write_text("from http.server import BaseHTTPRequestHandler, HTTPServer\nclass Handler(BaseHTTPRequestHandler):\n def do_GET(self):\n  self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(b'{\"status\":\"success\",\"data\":{\"alerts\":[]}}')\nHTTPServer(('0.0.0.0',9090), Handler).serve_forever()\n")
            command(["docker", "run", "-d", "--name", names["prom"], "--network", network, "--label", f"layertrace.cold-smoke={run_id}",
                     "--no-healthcheck", "-v", f"{stub}:/smoke/prometheus.py:ro", args.image, "python", "/smoke/prometheus.py"])
            command(["docker", "run", "-d", "--name", names["worker"], *common,
                     "--health-cmd", "python -c 'import os; os.kill(1,0)'", "--health-interval", "5s",
                     args.image, "python", "-m", "apps.control_plane.worker"])

            def projected():
                response = client.get("/api/v1/tasks/cold-task", headers=user)
                return response.json() if response.status_code == 200 and response.json()["task"]["status"] == "interrupted" else None

            task = wait_for(projected)
            # Chronological exit inference precedes the later client failure. The
            # append-only client fact strengthens diagnosis without rewriting it.
            check("worker_task_projection", task["task"]["status_provenance"] == "inferred", task["task"])
            check("client_failure_retained_as_observed", any(row["type"] == "task.failed" and row["provenance"] == "observed" for row in task["events"]))
            for row in task["events"]:
                check("event_identity_preserved_" + row["type"], all(row["attributes"].get(key) == value for key, value in identity.items()))
            check("free_text_pii_scrubbed", task["events"][-1]["attributes"]["message"] == "call [REDACTED:phone]")
            stored = command(["docker", "exec", names["db"], "psql", "-U", "smoke", "-d", "smoke", "-Atc",
                              "SELECT payload FROM events WHERE event_id='cold-task.failed'"])
            check("database_identity_preserved", all(json.loads(stored).get(key) == value for key, value in identity.items()))
            (directory / "task-detail.json").write_text(json.dumps(task, ensure_ascii=False, indent=2))

            def diagnosed():
                rows = client.get("/api/v1/incidents", headers=user).json()["items"]
                if not rows:
                    return None
                detail = client.get("/api/v1/incidents/" + rows[0]["id"], headers=user).json()
                return detail if detail.get("diagnosis") else None

            detail = wait_for(diagnosed)
            candidates = detail["diagnosis"]["candidates"]
            check("r0_process_exit_high", any(c["rule_id"] == "R0" and c["support_level"] == "high" for c in candidates))
            check("exit137_is_not_oom", not any(c["rule_id"] == "R1" and c["applicable"] for c in candidates))
            check("evidence_links_present", bool(detail["evidence"]))
            cited = detail["evidence"][0]
            evidence_response = client.get("/api/v1/evidence/" + cited["id"], headers=user)
            check("revision_evidence_lookup", evidence_response.status_code == 200 and evidence_response.json() == cited)
            check("probe_cannot_read_diagnostic_evidence", client.get("/api/v1/evidence/" + cited["id"], headers=collector).status_code == 403)
            (directory / "incident-open.json").write_text(json.dumps(detail, ensure_ascii=False, indent=2))
            incident_id = detail["id"]
            ack_path = f"/api/v1/incidents/{incident_id}:acknowledge"
            check("browser_write_requires_csrf", client.post(ack_path, json={"actor": "smoke"}).status_code == 403)
            check("browser_wrong_csrf_denied", client.post(ack_path, headers={"X-CSRF-Token": "invalid"}, json={"actor": "smoke"}).status_code == 403)
            check("browser_signed_csrf_write", client.post(ack_path, headers={"X-CSRF-Token": client.cookies["layertrace_csrf"]}, json={"actor": "smoke"}).status_code == 200)
            duplicate = client.post("/api/v1/events:batch", headers=collector, json=batch)
            check("duplicate_idempotency", duplicate.status_code == 202 and duplicate.json()["duplicate"] == 3 and duplicate.json()["accepted"] == 0)
            recovery = event("container.state", 0, "docker.container", cid, {"state": "running"})
            # Start the elapsed clock before the recovery event timestamp.
            recovered_at = time.monotonic()
            recovery["occurred_at"] = recovery["observed_at"] = datetime.now(UTC).isoformat()
            check("recovery_input", client.post("/api/v1/events:batch", headers=collector, json={"schema_version": "1.0", "events": [recovery]}).status_code == 202)

            def recovery_alert():
                value = client.get("/api/v1/incidents/" + incident_id, headers=user).json()
                return value if all(a["state"] == "resolved" for a in value["alerts"]) else None

            before_gate = wait_for(recovery_alert)
            check("stable_window_blocks_early_recovery", before_gate["state"] == "acknowledged")
            last_heartbeat = time.monotonic()

            def recovered():
                nonlocal last_heartbeat
                if time.monotonic() - last_heartbeat > 10:
                    check("recovery_heartbeat", heartbeat().status_code == 204)
                    last_heartbeat = time.monotonic()
                value = client.get("/api/v1/incidents/" + incident_id, headers=user).json()
                return value if value["state"] == "resolved" else None

            resolved = wait_for(recovered, timeout=80)
            elapsed = time.monotonic() - recovered_at
            check("real_60_second_recovery_gate", elapsed >= 60, round(elapsed, 2))
            (directory / "incident-resolved.json").write_text(json.dumps(resolved, ensure_ascii=False, indent=2))
            check("admin_revoke_204", client.post(f"/api/v1/probes/{probe_id}:revoke", headers=user).status_code == 204)
            check("revoked_heartbeat_denied", heartbeat().status_code == 403)
            check("revoked_installation_cannot_reregister", client.post("/api/v1/probes:register", headers=registration, json=probe_body).status_code == 403)
            check("browser_logout", client.post("/logout", headers={"X-CSRF-Token": client.cookies["layertrace_csrf"]}).status_code == 303)
            check("logged_out_read_denied", client.get("/api/v1/resources").status_code == 401)
        count = command(["docker", "exec", names["db"], "psql", "-U", "smoke", "-d", "smoke", "-Atc", "SELECT count(*) FROM event_receipts"])
        check("worker_receipts_committed", count == "4", count)
        check("worker_running", command(["docker", "inspect", names["worker"], "--format", "{{.State.Running}} {{.State.ExitCode}} {{.State.OOMKilled}}"] ) == "true 0 false")
        check("worker_healthy", command(["docker", "inspect", names["worker"], "--format", "{{.State.Health.Status}}"] ) == "healthy")
        check("worker_log_no_errors", " ERROR " not in command(["docker", "logs", names["worker"]]))
        report["success"] = True
    except Exception as error:
        report["error"] = scrub(f"{type(error).__name__}: {error}")
    finally:
        for key, name in names.items():
            output = command(["docker", "logs", name], required=False)
            (directory / f"{key}.log").write_text(output + "\n")
        for key in ("worker", "api", "prom", "db"):
            command(["docker", "rm", "-f", names[key]], required=False)
        command(["docker", "network", "rm", network], required=False)
        for filename in ("db.env", "runtime.env"):
            (directory / filename).unlink(missing_ok=True)
        leftovers = command(["docker", "ps", "-a", "--filter", f"label=layertrace.cold-smoke={run_id}", "--format", "{{.Names}}"])
        report["cleanup_verified"] = not leftovers
        report["finished_at"] = datetime.now(UTC).isoformat()
        (directory / "report.json").write_text(scrub(json.dumps(report, ensure_ascii=False, indent=2)) + "\n")
    print(json.dumps({"success": report["success"], "checks": len(report["checks"]), "report": str(directory / "report.json"), "cleanup_verified": report["cleanup_verified"], "error": report.get("error")}, ensure_ascii=False))
    return 0 if report["success"] and report["cleanup_verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
