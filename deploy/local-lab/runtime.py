#!/usr/bin/env python3
"""Recover the existing local WSL/ZSvirt lab without resetting resources or credentials."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    result = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip().strip("\"'")
    return result


def output(component: str, state: str, **facts):
    print(
        json.dumps({"component": component, "state": state, **facts}, ensure_ascii=False),
        flush=True,
    )


def run(args: list[str], *, content: str | None = None, timeout: int = 30) -> str:
    result = subprocess.run(args, input=content, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        # Never print subprocess stderr: remote tools may include request credentials.
        raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode})")
    return result.stdout


@contextmanager
def exclusive(path: Path):
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class Runtime:
    def __init__(self, config: Path):
        self.cfg = json.loads(config.read_text())
        self.root = Path(self.cfg["project_root"]).resolve()
        self.runtime = Path(self.cfg["runtime_directory"]).resolve()
        self.key = self.path("ssh_key")
        self.hosts = self.path("known_hosts")
        self.vm = self.cfg["vm_uuid"]
        if not re.fullmatch(r"[0-9a-f]{32}", self.vm):
            raise ValueError("Configure the exact 32-hex ZSvirt business VM UUID")
        address = ipaddress.ip_address(self.cfg["vm_ip"])
        network = ipaddress.ip_network("10.50.0.0/24")
        if address not in network or address in (
            network.network_address,
            network.broadcast_address,
        ):
            raise ValueError("This recovery entry is limited to the isolated 10.50.0.0/24 lab")
        if self.cfg["outer_ssh_port"] != 8822:
            raise ValueError("This local image uses the pinned outer SSH forwarding port 8822")
        for key in ("platform_rest_url", "api_url"):
            if urllib.parse.urlsplit(self.cfg[key]).hostname != "127.0.0.1":
                raise ValueError(f"{key} must use the local loopback forwarding boundary")
        self.tokens = None

    def path(self, name: str) -> Path:
        value = Path(self.cfg[name])
        return value.resolve() if value.is_absolute() else (self.runtime / value).resolve()

    def ssh(self, destination: str) -> list[str]:
        options = [
            "-i",
            str(self.key),
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=8",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={self.hosts}",
        ]
        outer = ["ssh", *options, "-p", str(self.cfg["outer_ssh_port"])]
        if destination == "outer":
            return [*outer, "root@127.0.0.1"]
        proxy = shlex.join([*outer, "-W", "%h:%p", "root@127.0.0.1"])
        return [
            "ssh",
            *options,
            "-o",
            f"ProxyCommand={proxy}",
            f"{self.cfg['vm_user']}@{self.cfg['vm_ip']}",
        ]

    def remote(self, destination: str, command: str, content: str | None = None) -> str:
        return run([*self.ssh(destination), command], content=content, timeout=60)

    @staticmethod
    def command(pid: int) -> list[str]:
        try:
            return [
                v
                for v in (Path("/proc") / str(pid) / "cmdline").read_bytes().decode().split("\0")
                if v
            ]
        except (OSError, UnicodeDecodeError):
            return []

    def owned_pid(self, name: str, required: list[str], *, adopt: bool = False) -> int | None:
        record = self.runtime / f"{name}.pid"
        try:
            pid = int(record.read_text())
            if all(value in self.command(pid) for value in required):
                return pid
        except (OSError, ValueError):
            pass
        found = [
            int(p.name)
            for p in Path("/proc").iterdir()
            if p.name.isdigit() and all(value in self.command(int(p.name)) for value in required)
        ]
        if len(found) > 1:
            raise RuntimeError(f"Multiple {name} processes match; no automatic takeover")
        if found and adopt:
            record.write_text(str(found[0]))
        return found[0] if found else None

    def qemu_signature(self) -> list[str]:
        return [
            "layertrace-zsvirt",
            f"file={self.path('overlay_disk')},if=virtio,format=qcow2,cache=writeback",
        ]

    def ensure_platform(self):
        pid = self.owned_pid("zsvirt", self.qemu_signature(), adopt=True)
        if pid:
            output("qemu", "running", pid=pid, action="retained")
            return
        overlay, original = self.path("overlay_disk"), self.path("original_disk")
        if overlay == original or not overlay.is_file() or not original.is_file():
            raise RuntimeError(
                "Both the existing overlay and its original backing image are required"
            )
        disk = json.loads(run(["qemu-img", "info", "-U", "--output=json", str(overlay)]))
        if Path(disk.get("full-backing-filename", "")).resolve() != original:
            raise RuntimeError(
                "The overlay backing path differs from the configured original image"
            )
        if not os.access("/dev/kvm", os.R_OK | os.W_OK):
            raise RuntimeError("KVM access is required; CPU emulation fallback is not enabled")
        forward = "user,id=mgmt,hostfwd=tcp:127.0.0.1:8822-:22,hostfwd=tcp:127.0.0.1:8843-:443,hostfwd=tcp:127.0.0.1:8880-:8080,hostfwd=tcp:127.0.0.1:8850-:5000"
        run(
            [
                "qemu-system-x86_64",
                "-name",
                "layertrace-zsvirt",
                "-machine",
                "q35,accel=kvm",
                "-cpu",
                "host",
                "-smp",
                str(self.cfg["qemu_vcpus"]),
                "-m",
                str(self.cfg["qemu_memory_mib"]),
                "-drive",
                self.qemu_signature()[1],
                "-netdev",
                forward,
                "-device",
                "virtio-net-pci,netdev=mgmt,mac=52:54:00:ab:10:01",
                "-display",
                "vnc=127.0.0.1:19",
                "-serial",
                f"file:{self.runtime / 'serial.log'}",
                "-qmp",
                f"unix:{self.runtime / 'qmp.sock'},server=on,wait=off",
                "-pidfile",
                str(self.runtime / "zsvirt.pid"),
                "-daemonize",
            ]
        )
        output("qemu", "started", pid=self.owned_pid("zsvirt", self.qemu_signature()))

    def wait_ssh(self, destination: str):
        deadline = time.monotonic() + self.cfg["boot_timeout_seconds"]
        while time.monotonic() < deadline:
            try:
                self.remote(destination, "true")
                output(destination + "-ssh", "ready")
                return
            except (RuntimeError, subprocess.TimeoutExpired):
                time.sleep(2)
        raise RuntimeError(f"{destination} SSH did not become ready; the VM was not restarted")

    def install_network(self):
        script = (self.root / "deploy/local-lab/network.sh").read_text()
        unit = """[Unit]
Description=LayerTrace isolated business bridge and NAT
Wants=network-online.target
After=network-online.target
Before=zstack.service libvirtd.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/layertrace-lab-network apply
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
        payload = json.dumps(
            {
                "/usr/local/sbin/layertrace-lab-network": [script, 0o755],
                "/etc/systemd/system/layertrace-lab-network.service": [unit, 0o644],
                "/etc/sysctl.d/99-layertrace-lab.conf": ["net.ipv4.ip_forward=1\n", 0o644],
            }
        )
        code = "import sys,json,pathlib,os;d=json.load(sys.stdin);\nfor name,(text,mode) in d.items():\n p=pathlib.Path(name);p.parent.mkdir(parents=True,exist_ok=True);\n if not p.exists() or p.read_text()!=text:\n  q=p.with_suffix(p.suffix+'.layertrace-tmp');q.write_text(text);q.chmod(mode);q.replace(p)\n"
        self.remote("outer", shlex.join(["python3", "-c", code]), payload)
        self.remote(
            "outer",
            "systemctl daemon-reload && systemctl enable layertrace-lab-network.service >/dev/null && /usr/local/sbin/layertrace-lab-network apply",
        )
        output("lab-network", "ready", bridge="br_lab0", address="10.50.0.1/24", persistent=True)

    def request(self, method: str, url: str, body: dict | None = None, headers: dict | None = None):
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.load(response), dict(response.headers)
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except ValueError:
                payload = {}
            return exc.code, payload, dict(exc.headers)

    def platform_call(self, method: str, path: str, body: dict | None = None):
        if self.tokens is None:
            self.tokens = read_env(self.path("platform_secret_env"))
        url = self.cfg["platform_rest_url"] + path
        for attempt in range(2):
            status, data, headers = self.request(
                method, url, body, {"Authorization": "OAuth " + self.tokens.get("ZSVIRT_TOKEN", "")}
            )
            code = (data.get("error") or {}).get("code")
            if (status == 401 or code == "ID.1001") and attempt == 0:
                password = self.tokens.get("ZSVIRT_ADMIN_PASSWORD")
                if not password:
                    raise RuntimeError(
                        "A protected ZSVIRT_ADMIN_PASSWORD is needed to renew the operator session"
                    )
                login = {
                    "logInByAccount": {
                        "accountName": self.cfg["platform_account"],
                        "password": hashlib.sha512(password.encode()).hexdigest(),
                    }
                }
                login_status, login_data, _ = self.request(
                    "PUT", self.cfg["platform_rest_url"] + "/accounts/login", login
                )
                if login_status != 200 or not login_data.get("inventory", {}).get("uuid"):
                    raise RuntimeError("ZSvirt operator session renewal failed")
                self.tokens["ZSVIRT_TOKEN"] = login_data["inventory"]["uuid"]
                continue
            if status >= 400 or data.get("error"):
                raise RuntimeError(
                    f"ZSvirt operation failed (HTTP {status}, code {code or 'unknown'})"
                )
            location = data.get("location") or headers.get("Location")
            deadline = time.monotonic() + self.cfg["boot_timeout_seconds"]
            while status == 202:
                if not location or time.monotonic() >= deadline:
                    raise RuntimeError("ZSvirt operation completion could not be confirmed")
                parsed = urllib.parse.urlsplit(location)
                if parsed.hostname not in (None, "127.0.0.1", "10.0.2.15"):
                    raise RuntimeError("Unexpected ZSvirt completion URL")
                base = urllib.parse.urlsplit(self.cfg["platform_rest_url"])
                poll_url = urllib.parse.urlunsplit(
                    (base.scheme, base.netloc, parsed.path, parsed.query, "")
                )
                time.sleep(1)
                status, data, headers = self.request(
                    "GET",
                    poll_url,
                    headers={"Authorization": "OAuth " + self.tokens["ZSVIRT_TOKEN"]},
                )
                if status >= 400 or data.get("error"):
                    raise RuntimeError("ZSvirt asynchronous operation failed")
                location = data.get("location") or headers.get("Location") or location
            return data
        raise RuntimeError("ZSvirt session renewal did not authorize the operation")

    def wait_platform(self):
        deadline = time.monotonic() + self.cfg["boot_timeout_seconds"]
        while time.monotonic() < deadline:
            try:
                status, data, _ = self.request(
                    "PUT", self.cfg["platform_rest_url"] + "/management-nodes/actions",
                    {"getVersion": {}},
                )
                if status == 200 and data.get("version"):
                    output("zsvirt-api", "ready", version=data["version"])
                    return
            except (OSError, ValueError):
                pass
            time.sleep(2)
        raise RuntimeError("ZSvirt API did not become ready within the boot timeout")

    def ensure_business_vm(self):
        deadline = time.monotonic() + self.cfg["boot_timeout_seconds"]
        while True:
            data = self.platform_call("GET", "/vm-instances/" + self.vm)
            inventories = data.get("inventories") or [data.get("inventory")]
            vm = next((v for v in inventories if v and v.get("uuid") == self.vm), None)
            if not vm:
                raise RuntimeError(
                    "Configured business VM is absent; recovery never creates a replacement"
                )
            if vm["state"] not in {"Unknown", "Starting", "Stopping", "Rebooting"}:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    f"Business VM remains {vm['state']} after the boot timeout; no force operation"
                )
            time.sleep(2)
        if vm["state"] == "Stopped":
            self.platform_call("PUT", f"/vm-instances/{self.vm}/actions", {"startVmInstance": {}})
            output("business-vm", "started", uuid=self.vm)
        elif vm["state"] == "Running":
            output("business-vm", "running", uuid=self.vm, action="retained")
        else:
            raise RuntimeError(f"Business VM is {vm['state']}; no automatic force operation")

    def ensure_database(self):
        settings = read_env(self.path("control_plane_env"))
        uri = urllib.parse.urlsplit(
            settings["DATABASE_URL"].replace("postgresql+asyncpg:", "postgresql:")
        )
        if uri.hostname != "127.0.0.1":
            raise RuntimeError(
                "Native cluster recovery is limited to the configured local PostgreSQL"
            )
        check = subprocess.run(
            ["pg_isready", "-q", "-h", "127.0.0.1", "-p", str(uri.port or 5432)],
            capture_output=True,
        )
        if check.returncode == 0:
            output("postgres", "ready", action="retained")
            return
        version, cluster = self.cfg["postgres_cluster"].split("/", 1)
        run(["pg_ctlcluster", version, cluster, "start"], timeout=60)
        output("postgres", "started", cluster=self.cfg["postgres_cluster"])

    def spawn(self, name: str, args: list[str], env: dict | None = None):
        fd = os.open(self.runtime / f"{name}.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "ab") as log:
            proc = subprocess.Popen(
                args,
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
                close_fds=True,
            )
        (self.runtime / f"{name}.pid").write_text(str(proc.pid))
        output(name, "started", pid=proc.pid)

    def ensure_control(self):
        settings = read_env(self.path("control_plane_env"))
        if not settings.get("API_TOKENS") or not settings.get("PROBE_REGISTRATION_SECRET"):
            raise RuntimeError("Protected control-plane credentials are missing")
        python = str(self.root / ".venv/bin/python")
        commands = {
            "web": [
                python,
                "-m",
                "uvicorn",
                "apps.control_plane.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            "worker": [python, "-m", "apps.control_plane.worker"],
        }
        missing = {}
        for name, args in commands.items():
            pid = self.owned_pid(name, args, adopt=True)
            if pid:
                output(name, "running", pid=pid, action="retained")
            else:
                missing[name] = args
        if not missing:
            return
        env = {**os.environ, **settings}
        # Existing data is retained. Only committed schema migrations are applied.
        result = subprocess.run(
            [python, "-m", "alembic", "upgrade", "head"],
            cwd=self.root,
            env=env,
            capture_output=True,
            timeout=60,
        )
        if result.returncode:
            raise RuntimeError("Control-plane schema migration failed; services were not started")
        for name, args in missing.items():
            self.spawn(name, args, env)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if any(not self.owned_pid(name, args) for name, args in commands.items()):
                raise RuntimeError("A control-plane process exited; inspect its private runtime log")
            try:
                status, _data, _headers = self.request(
                    "GET",
                    self.cfg["api_url"] + "/api/v1/status",
                    headers={"Authorization": "Bearer " + settings["API_TOKENS"].split(",")[0]},
                )
                if status == 200:
                    output("control-plane", "ready", http_status=status)
                    return
            except (OSError, ValueError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Started control-plane processes did not become HTTP-ready")

    def ensure_prometheus(self):
        name = self.cfg["prometheus_name"]
        result = subprocess.run(["docker", "inspect", name], capture_output=True, text=True)
        if result.returncode == 0:
            container = json.loads(result.stdout)[0]
            if "--web.listen-address=127.0.0.1:19090" not in container["Config"]["Cmd"]:
                raise RuntimeError("Prometheus name belongs to an unexpected container")
            if not container["State"]["Running"]:
                run(["docker", "start", container["Id"]])
                output("prometheus", "started")
            else:
                output("prometheus", "running", action="retained")
            return
        directory = self.runtime / "prometheus"
        directory.mkdir(exist_ok=True)
        config = directory / "prometheus.yml"
        if not config.exists():
            config.write_text(
                "global:\n  scrape_interval: 5s\n  evaluation_interval: 5s\nscrape_configs:\n  - job_name: prometheus\n    static_configs:\n      - targets: ['127.0.0.1:19090']\n  - job_name: vm-probe\n    static_configs:\n      - targets: ['127.0.0.1:19108']\n"
            )
        data = directory / "data"
        data.mkdir(exist_ok=True)
        os.chown(data, 65534, 65534)
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--network",
                "host",
                "--restart",
                "unless-stopped",
                "-v",
                str(config) + ":/etc/prometheus/prometheus.yml:ro",
                "-v",
                str(data) + ":/prometheus",
                self.cfg["prometheus_image"],
                "--config.file=/etc/prometheus/prometheus.yml",
                "--web.listen-address=127.0.0.1:19090",
            ]
        )
        output("prometheus", "started")

    def tunnel_signature(self) -> list[str]:
        return [
            "127.0.0.1:18000:127.0.0.1:8000",
            "127.0.0.1:19108:127.0.0.1:9108",
            "127.0.0.1:18091:127.0.0.1:8091",
            f"{self.cfg['vm_user']}@{self.cfg['vm_ip']}",
        ]

    def ensure_tunnel(self):
        pid = self.owned_pid("tunnel", self.tunnel_signature(), adopt=True)
        if pid:
            output("tunnel", "running", pid=pid, action="retained")
            return
        args = [
            *self.ssh("vm")[:-1],
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=15",
            "-o",
            "ServerAliveCountMax=3",
            "-N",
            "-R",
            self.tunnel_signature()[0],
            "-L",
            self.tunnel_signature()[1],
            "-L",
            self.tunnel_signature()[2],
            self.ssh("vm")[-1],
        ]
        self.spawn("tunnel", args)
        time.sleep(0.5)
        if not self.owned_pid("tunnel", self.tunnel_signature()):
            raise RuntimeError("SSH forwarding failed; inspect the private tunnel log")

    def stop_tunnel(self):
        pid = self.owned_pid("tunnel", self.tunnel_signature())
        if not pid:
            output("tunnel", "stopped", action="retained")
            return
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + 8
        while self.command(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if self.command(pid):
            raise RuntimeError("Owned SSH process did not stop; no force kill was performed")
        (self.runtime / "tunnel.pid").unlink(missing_ok=True)
        output("tunnel", "stopped", pid=pid)

    def resume_workload(self):
        source = self.cfg["vm_source_directory"]
        checker = "import pathlib,json;\nfor p in pathlib.Path('evidence').rglob('state.json'):\n try:\n  s=json.loads(p.read_text())\n except (OSError,ValueError):\n  continue\n if s.get('state') in ('prepared','injecting','running','restoring') and s.get('before',{}).get('container_id'):\n  raise SystemExit('Active laboratory state prevents automatic workload recovery')\n if s.get('mode') in ('on','off') and not s.get('restored'):\n  raise SystemExit('Active probe measurement prevents automatic workload recovery')\n"
        self.remote(
            "vm", "cd " + shlex.quote(source) + " && " + shlex.join(["python3", "-c", checker])
        )
        args = [
            "sudo",
            "docker",
            "compose",
            "--project-name",
            "layertrace-vm",
            "--env-file",
            self.cfg["vm_workload_env"],
            "-f",
            "deploy/vm-probe/compose.yaml",
            "-f",
            "deploy/workload/compose.yaml",
            "-f",
            "deploy/vm-probe/compose.host-network.yaml",
            "-f",
            "deploy/workload/compose.offline.yaml",
            "-f",
            "deploy/workload/compose.client-fixed.yaml",
            "-f",
            "deploy/vm-probe/compose.final-images.yaml",
            "up",
            "-d",
            "--no-build",
            "--pull",
            "never",
        ]
        self.remote("vm", "cd " + shlex.quote(source) + " && " + shlex.join(args))
        output("workload", "resumed", compose_files=6)

    def status(self):
        for name, required in (
            ("qemu", self.qemu_signature()),
            ("tunnel", self.tunnel_signature()),
            (
                "web",
                [str(self.root / ".venv/bin/python"), "uvicorn", "apps.control_plane.main:app"],
            ),
            ("worker", [str(self.root / ".venv/bin/python"), "apps.control_plane.worker"]),
        ):
            pid = self.owned_pid("zsvirt" if name == "qemu" else name, required)
            output(name, "running" if pid else "stopped", pid=pid)
        try:
            self.remote("outer", "true")
            script = (self.root / "deploy/local-lab/network.sh").read_text()
            self.remote("outer", "bash -s -- check", script)
            output("lab-network", "ready")
        except Exception as exc:
            output("lab-network", "unavailable", error=type(exc).__name__)
        settings = read_env(self.path("control_plane_env"))
        try:
            status, data, _ = self.request(
                "GET",
                self.cfg["api_url"] + "/api/v1/status",
                headers={"Authorization": "Bearer " + settings.get("API_TOKENS", "").split(",")[0]},
            )
            output(
                "control-plane",
                "ready" if status == 200 else "unavailable",
                http_status=status,
                sources=[{"type": s["type"], "state": s["state"]} for s in data.get("sources", [])],
            )
        except Exception as exc:
            output("control-plane", "unavailable", error=type(exc).__name__)
        for name, port, path in (
            ("prometheus", 19090, "/-/ready"),
            ("probe-metrics", 19108, "/metrics"),
            ("agent-health", 18091, "/healthz"),
        ):
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=5
                ) as response:
                    output(name, "ready", http_status=response.status)
            except Exception as exc:
                output(name, "unavailable", error=type(exc).__name__)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["status", "up", "tunnel-up", "tunnel-down", "network-install"]
    )
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("runtime.json"))
    parser.add_argument(
        "--resume-workload",
        action="store_true",
        help="Explicitly resume the five-file business Compose project including the independent client",
    )
    args = parser.parse_args()
    lab = Runtime(args.config.resolve())
    if args.action == "status":
        lab.status()
        return
    if os.geteuid() != 0:
        raise RuntimeError("Use sudo for managed QEMU/native PostgreSQL recovery")
    with exclusive(lab.runtime / "runtime.lock"):
        if args.action == "tunnel-down":
            lab.stop_tunnel()
        elif args.action == "tunnel-up":
            lab.ensure_tunnel()
        elif args.action == "network-install":
            lab.install_network()
        else:
            lab.ensure_platform()
            lab.wait_ssh("outer")
            lab.install_network()
            lab.wait_platform()
            lab.ensure_business_vm()
            lab.wait_ssh("vm")
            lab.ensure_database()
            lab.ensure_control()
            lab.ensure_prometheus()
            lab.ensure_tunnel()
            if args.resume_workload:
                lab.resume_workload()
            lab.status()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        output("recovery", "failed", error=str(error))
        raise SystemExit(1) from None
