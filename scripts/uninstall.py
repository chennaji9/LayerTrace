#!/usr/bin/env python3
"""Revoke collector credentials before stopping a selected Compose deployment."""

from __future__ import annotations

import argparse
import json
import ssl
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

ROOT = Path(__file__).resolve().parents[1]
CREDENTIAL_PATH = "/var/lib/layertrace/probe-credential.json"


class UninstallError(Exception):
    pass


def load_env(path: Path) -> dict[str, str]:
    if path.is_symlink() or not path.is_file():
        raise UninstallError("Expected a protected regular environment file")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class API:
    def __init__(self, url: str, token: str, *, ca_file: str | None = None):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise UninstallError("Invalid control-plane URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise UninstallError("HTTP requires a local SSH tunnel; use trusted HTTPS otherwise")
        if not token:
            raise UninstallError("Administrator API token is required for server revocation")
        self.url, self.token = url.rstrip("/"), token
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def request(self, method: str, path: str, *, token: str | None = None, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(self.url + path, data=data, method=method, headers={
            "Authorization": "Bearer " + (token or self.token), "Content-Type": "application/json",
        })
        try:
            with self.opener.open(request, timeout=20) as response:
                payload = response.read(1024 * 1024 + 1)
                if len(payload) > 1024 * 1024:
                    raise UninstallError("Control-plane response exceeds the bounded limit")
                return response.status, json.loads(payload) if payload else {}
        except HTTPError as exc:
            return exc.code, {}
        except (URLError, TimeoutError, OSError) as exc:
            raise UninstallError("Control-plane connection failed; deployment and credentials retained") from exc

    def revoke(self, probe_id: str, *, old_credential: str | None = None) -> None:
        probe_id = str(uuid.UUID(probe_id))
        status, _ = self.request("POST", f"/api/v1/probes/{probe_id}:revoke")
        if status != 204:
            raise UninstallError(f"Server revocation returned HTTP {status}; stopping was refused")
        if old_credential:
            status, _ = self.request("POST", f"/api/v1/probes/{probe_id}:heartbeat", token=old_credential, body={
                "observed_at": datetime.now(UTC).isoformat(), "queue_depth": 0, "dropped_count": 0,
            })
            if status not in {401, 403}:
                raise UninstallError(f"Old probe credential was not confirmed rejected (HTTP {status}); stopping was refused")

    def assert_probe_scope(self, probe_id: str, *, vm_external_id: str, installation_id: str) -> None:
        status, content = self.request("GET", "/api/v1/status")
        if status != 200 or not isinstance(content.get("sources"), list):
            raise UninstallError("Could not confirm the selected collector's server identity")
        source = next((row for row in content["sources"] if row.get("type") == "probe" and row.get("instance") == probe_id), None)
        capabilities = source.get("capabilities", {}) if source else {}
        if not vm_external_id or not installation_id or capabilities.get("vm_source_id") != vm_external_id or capabilities.get("installation_id") != installation_id:
            raise UninstallError("Collector credential does not match the selected VM and installation; stopping was refused")


def run(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise UninstallError("Docker Compose command failed; inspect the selected deployment locally")
    return result.stdout


def parse_credential(value: str) -> dict:
    data = json.loads(value)
    uuid.UUID(data["probe_id"])
    if not isinstance(data.get("credential"), str) or len(data["credential"]) < 16:
        raise UninstallError("Probe credential file is invalid")
    return data


def delete_exact_file(path: Path) -> None:
    if path.is_symlink() or path.exists() and not path.is_file():
        raise UninstallError("Refusing to delete a linked or non-file credential path")
    path.unlink(missing_ok=True)


def clear_collector_env(path: Path) -> None:
    keys = {"PROBE_REGISTRATION_SECRET", "PROBE_REGISTRATION_TOKEN", "LAYERTRACE_TOKEN"}
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        lines.append(key + "=" if key in keys and not line.lstrip().startswith("#") else line)
    temporary = path.with_name(path.name + ".uninstall.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        temporary.chmod(0o600)
        stream.write("\n".join(lines) + "\n")
    temporary.replace(path)


def uninstall(args) -> dict:
    env_file = Path(args.env_file or (ROOT / "deploy/vm-probe/.env" if args.target == "vm" else ROOT / ".env")).absolute()
    config = load_env(env_file)
    token = config.get("LAYERTRACE_TOKEN") or config.get("API_TOKENS", "").split(",")[0].strip()
    api_url = args.api_url or config.get("LAYERTRACE_API") or "http://127.0.0.1:8000"
    ca_file = args.ca_file
    if not ca_file and config.get("LAYERTRACE_CA_FILE"):
        candidate = Path(config["LAYERTRACE_CA_FILE"])
        if not candidate.is_file():
            directory = Path(config.get("LAYERTRACE_CA_DIRECTORY", "deploy/vm-probe/certs"))
            candidate = (directory if directory.is_absolute() else ROOT / directory) / candidate.name
        if not candidate.is_file():
            raise UninstallError("Private CA is missing; pass its host path with --ca-file")
        ca_file = str(candidate)
    api = API(api_url, token, ca_file=ca_file)
    compose = ["docker", "compose", "--env-file", str(env_file)]
    if args.target == "vm":
        compose += ["--project-name", "layertrace-vm", "-f", str(ROOT / "deploy/vm-probe/compose.yaml"), "-f", str(ROOT / "deploy/workload/compose.yaml")]
    else:
        compose += ["-f", str(ROOT / "deploy/compose.yaml")]
    for value in args.compose_overlay:
        overlay = Path(value).absolute()
        if not overlay.is_file() or overlay.is_symlink():
            raise UninstallError("Expected an existing regular Compose overlay")
        compose += ["-f", str(overlay)]
    run([*compose, "config", "--quiet"])
    revoked = []
    if args.target == "vm":
        credential_file = Path(args.credential_file).absolute() if args.credential_file else None
        if credential_file:
            if credential_file.is_symlink() or not credential_file.is_file():
                raise UninstallError("Expected a regular exported probe credential file")
            credential = parse_credential(credential_file.read_text(encoding="utf-8"))
        else:
            credential = parse_credential(run([*compose, "exec", "-T", "vm-probe", "python", "-c", f"from pathlib import Path;print(Path({CREDENTIAL_PATH!r}).read_text())"]))
        api.assert_probe_scope(
            credential["probe_id"], vm_external_id=config.get("PROBE_VM_EXTERNAL_ID", ""),
            installation_id=config.get("PROBE_INSTALLATION_ID", ""),
        )
        is_running = "vm-probe" in run([*compose, "ps", "--status", "running", "--services"]).splitlines()
        if is_running:
            run([*compose, "pause", "vm-probe"])
        try:
            api.revoke(credential["probe_id"], old_credential=credential["credential"])
        except Exception:
            if is_running:
                run([*compose, "unpause", "vm-probe"])
            raise
        revoked.append(credential["probe_id"])
        run([*compose, "down", "--remove-orphans"])
        # Same named state volume, same fixed file; SQLite outbox and business results remain.
        run([*compose, "run", "--rm", "--no-deps", "--pull", "never", "--no-TTY", "--entrypoint", "python", "vm-probe", "-c", f"from pathlib import Path;Path({CREDENTIAL_PATH!r}).unlink(missing_ok=True)"])
        run([*compose, "down", "--remove-orphans"])
        if credential_file:
            delete_exact_file(credential_file)
        clear_collector_env(env_file)
    else:
        if args.credential_file:
            raise UninstallError("Exported credential-file is only used for the VM target")
        status, content = api.request("GET", "/api/v1/status")
        if status != 200 or not isinstance(content.get("sources"), list):
            raise UninstallError("Could not enumerate scoped probes; stopping was refused")
        for source in content["sources"]:
            if source.get("type") == "probe":
                api.revoke(source["instance"])
                revoked.append(source["instance"])
        run([*compose, "down", "--remove-orphans"])
        for name in (".probe-registration-token", ".probe-registration-secret"):
            delete_exact_file(ROOT / name)
    return {"target": args.target, "server_revoked_probe_ids": revoked, "data_volumes_retained": True, "data_deletion_entry": "deploy/purge-data.sh"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["vm", "control-plane"])
    parser.add_argument("--env-file")
    parser.add_argument("--api-url", help="Reachable trusted HTTPS or local SSH-tunnel URL")
    parser.add_argument("--ca-file", help="Host path of the trusted private CA")
    parser.add_argument("--compose-overlay", action="append", default=[])
    parser.add_argument("--credential-file", help="Protected JSON exported from this probe when container exec is unavailable")
    args = parser.parse_args()
    try:
        print(json.dumps(uninstall(args), ensure_ascii=False, indent=2))
        return 0
    except (UninstallError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        message = str(exc) if isinstance(exc, UninstallError) else "Invalid input or local command failure; inspect protected configuration locally"
        print("Uninstall failed: " + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
