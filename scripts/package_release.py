#!/usr/bin/env python3
"""Create and verify isolated LayerTrace delivery archives using the standard library.

Run ``python3 scripts/package_release.py create --help`` for packaging inputs.
No working-tree Git operations, external publication or large platform images occur.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

SOURCE_DIRECTORIES = (
    "apps", "agents", "packages", "integrations", "deploy", "rules", "scenarios",
    "workloads", "tests", "fixtures", "specs", "scripts", "docs",
)
ROOT_FILES = (
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "alembic.ini",
    "pytest.ini", ".env.example", ".gitignore", ".dockerignore",
)
REQUIRED_SOURCE = (
    "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "alembic.ini",
    "apps/control_plane/main.py", "apps/control_plane/worker.py",
    "apps/control_plane/web/routes.py", "apps/control_plane/web/static/app.js",
    "apps/control_plane/web/templates/base.html", "agents/vm_probe/__main__.py",
    "packages/contracts/schemas.py", "integrations/zsvirt/provider.py",
    "deploy/compose.yaml", "deploy/Dockerfile.control-plane", "deploy/Dockerfile.probe",
    "deploy/vm-probe/compose.yaml", "deploy/vm-probe/env.example",
    "deploy/vm-probe/install.sh", "deploy/workload/compose.yaml",
    "deploy/workload/fetch-model.sh", "deploy/models/manifest.yaml",
    "rules/R1-container-oom.yaml", "rules/R2-cpu-quota.yaml", "rules/R3-tool-failure.yaml",
    "scenarios/real_lab.py", "workloads/demo_agent/agent.py",
    "workloads/demo_agent/tool_server.py", "workloads/demo_agent/load_generator.py",
    "tests/conftest.py", "fixtures/story-oom.json", "scripts/package_release.py",
    "scripts/configure_local_env.py", "scripts/uninstall.py", "deploy/uninstall.sh",
    "deploy/install.sh", "deploy/purge-data.sh",
    "docs/ui-operation-guide.md", "docs/video-narration.md",
)
FORBIDDEN_DIRECTORIES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", ".cache", "htmlcov", "runtime", "secrets",
    "credentials", "certs", "private", "dist", "build", ".idea", ".vscode",
    "delivery", "releases", "artifacts", "logs", "data",
})
PLATFORM_SUFFIXES = frozenset({".qcow2", ".vhd", ".vhdx", ".vmdk", ".ova", ".iso"})
EXTERNAL_SUFFIXES = PLATFORM_SUFFIXES | frozenset({
    ".gguf", ".safetensors", ".pt", ".pth", ".onnx", ".bin", ".tar", ".tgz",
    ".zip", ".bundle", ".mp4", ".mkv", ".webm", ".mov", ".sqlite", ".sqlite3", ".db",
})
SECRET_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx", ".crt"})
IGNORED_SUFFIXES = frozenset({".pyc", ".pyo", ".log", ".tmp", ".swp", ".bak"})
NON_DELIVERY_DOCS = frozenset({
    "handoff-codex.md", "ZSvirt-参赛差距分析与冲刺方案.md", "ZSvirt-MVP设计方案.md",
})
SENSITIVE_KEY = re.compile(r"(?:token|secret|password|authorization|api[_-]?key|access[_-]?key|cookie|private[_-]?key)", re.I)
AUTH_VALUE = re.compile(r"\b(?:Bearer|OAuth|ZStack)\s+([A-Za-z0-9._:+/=-]{8,})", re.I)
PLACEHOLDER = re.compile(r"(?:redact|hidden|placeholder|example|change.?me|replace|not.configured|NOT_DISTRIBUTED|已隐藏|已脱敏|示例|替换|your[_ -])", re.I)
PLACEHOLDER_DOC = re.compile(r"\[(?:实测|交付)回填[：:]")
LICENSE_NAME = re.compile(r"(?:^|[_.-])licen[cs]e(?:$|[_.-])", re.I)
TEXT_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*[:=]\s*(.*?)\s*$")
MAX_SOURCE_FILE_BYTES = 20 * 1024 * 1024
MAX_COMPANION_FILE_BYTES = 1024 * 1024


class ReleaseError(Exception):
    pass


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_member(name: str) -> str:
    """Reject traversal, Windows drive/UNC names and ambiguous extraction paths."""
    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
        raise ReleaseError(f"Unsafe archive member: {name!r}")
    path = PurePosixPath(name)
    if any(part in {"", ".", ".."} or ":" in part for part in name.split("/")):
        raise ReleaseError(f"Unsafe archive member: {name!r}")
    if path.is_absolute():
        raise ReleaseError(f"Unsafe archive member: {name!r}")
    return path.as_posix()


def regular_file(value: str | Path) -> Path:
    path = Path(value).absolute()
    if path.is_symlink() or not path.is_file():
        raise ReleaseError(f"Expected a regular file: {path}")
    if path.suffix.lower() in PLATFORM_SUFFIXES:
        raise ReleaseError(f"Platform virtual disks are excluded from release packages: {path.name}")
    return path


def _template_value(value: str) -> bool:
    value = value.strip()
    return not value or value.startswith(("<", "${", "$", "[")) or bool(PLACEHOLDER.search(value))


def load_known_secrets(source: Path, explicit: list[str]) -> list[bytes]:
    """Read local configuration only to detect accidental copies; never emit values."""
    candidates = [source / ".env", *[Path(path) for path in explicit]]
    for directory in (source / "deploy/vm-probe", source / "deploy/workload"):
        candidates.append(directory / ".env")
    values: set[bytes] = set()
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            if str(path) in explicit:
                raise ReleaseError(f"Secret scan input does not exist: {path}")
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#") or "=" not in line:
                continue
            key, raw = line.split("=", 1)
            if not SENSITIVE_KEY.search(key):
                continue
            raw = raw.strip().strip("\"'")
            for value in raw.split(","):
                if len(value) >= 8 and not _template_value(value):
                    values.add(value.encode())
    return sorted(values)


def check_content(path: Path, *, secrets: list[bytes], evidence: bool = False, final: bool = False) -> None:
    if path.stat().st_size > MAX_SOURCE_FILE_BYTES and not evidence:
        raise ReleaseError(f"Source file exceeds 20 MiB; place assets in a separate package: {path.name}")
    private_markers = (b"-----BEGIN PRIVATE KEY-----", b"-----BEGIN OPENSSH PRIVATE KEY-----", b"-----BEGIN RSA PRIVATE KEY-----")
    retained = max([len(value) for value in secrets + list(private_markers)], default=64)
    tail = b""
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            window = tail + chunk
            if any(secret in window for secret in secrets):
                raise ReleaseError(f"Configured credential detected; redact or exclude file: {path}")
            if any(re.search(rb"(?:^|\n)" + re.escape(marker), window) for marker in private_markers):
                raise ReleaseError(f"Private key material detected: {path}")
            tail = window[-retained:]
    if path.suffix.lower() not in {".json", ".jsonl", ".yaml", ".yml", ".txt", ".md", ".csv", ".tsv", ".log", ".env"}:
        return
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ReleaseError(f"Text evidence exceeds 64 MiB; split bounded evidence files: {path}")
    text = path.read_text(encoding="utf-8", errors="replace")
    if final and path.suffix.lower() == ".md" and PLACEHOLDER_DOC.search(text):
        raise ReleaseError(f"Unfilled delivery record in documentation: {path}")
    if not evidence:
        return
    for match in AUTH_VALUE.finditer(text):
        if not _template_value(match.group(1)):
            raise ReleaseError(f"Authorization credential detected in evidence: {path}")

    def inspect(item) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if SENSITIVE_KEY.search(str(key)) and isinstance(value, str) and len(value) >= 8 and not _template_value(value):
                    raise ReleaseError(f"Non-redacted credential field in evidence: {path}")
                inspect(value)
        elif isinstance(item, list):
            for value in item:
                inspect(value)

    if path.suffix.lower() in {".json", ".jsonl"}:
        try:
            for line in text.splitlines() if path.suffix.lower() == ".jsonl" else [text]:
                if line.strip():
                    inspect(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ReleaseError(f"Invalid JSON evidence in {path.name}: {exc.msg}") from exc


def exclusion(relative: str) -> str | None:
    path = PurePosixPath(relative)
    parts = path.parts
    if any(part.lower() in FORBIDDEN_DIRECTORIES or part.endswith(".egg-info") for part in parts[:-1]):
        return "local state or credentials directory"
    if path.name in {".env", ".DS_Store", "Thumbs.db", ".coverage", ".gitkeep"}:
        return "local state"
    if path.name.startswith(".env.") and path.name != ".env.example":
        return "local environment"
    if path.suffix.lower() in EXTERNAL_SUFFIXES | SECRET_SUFFIXES | IGNORED_SUFFIXES:
        return "external asset, credential, cache or runtime artifact"
    if parts[:2] == ("docs", "screenshots"):
        return "screenshots require explicit inclusion"
    if parts[0] == "docs" and path.name in NON_DELIVERY_DOCS:
        return "working or planning document"
    if path.name.lower().startswith(("id_rsa", "id_ed25519", "credentials", "secret-")):
        return "credential filename"
    return None


def source_files(source: Path, secrets: list[bytes], *, final: bool) -> tuple[dict[str, Path], list[dict]]:
    files: dict[str, Path] = {}
    excluded: list[dict] = []
    candidates = [source / name for name in ROOT_FILES]
    for name in SOURCE_DIRECTORIES:
        directory = source / name
        if not directory.is_dir() or directory.is_symlink():
            raise ReleaseError(f"Core source directory is missing or linked: {name}")
        for current, directories, names in os.walk(directory, followlinks=False):
            directories[:] = sorted(d for d in directories if d.lower() not in FORBIDDEN_DIRECTORIES and not d.endswith(".egg-info") and not (Path(current) / d).is_symlink())
            candidates.extend(Path(current) / filename for filename in sorted(names))
    for path in sorted(candidates):
        relative = path.relative_to(source).as_posix()
        reason = exclusion(relative)
        if path.is_symlink():
            reason = "symbolic link"
        if reason:
            excluded.append({"path": relative, "reason": reason})
            continue
        if not path.is_file():
            raise ReleaseError(f"Core source file is missing: {relative}")
        check_content(path, secrets=secrets, final=final)
        files[relative] = path
    missing = sorted(set(REQUIRED_SOURCE) - files.keys())
    if missing:
        raise ReleaseError("Missing core assets: " + ", ".join(missing))
    return files, excluded


def add_file(files: dict[str, Path], relative: str, path: Path) -> None:
    relative = safe_member(relative)
    if relative in files:
        raise ReleaseError(f"Duplicate archive path: {relative}")
    files[relative] = path


def material_files(args, secrets: list[bytes]) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for name, values in (("documents", args.pdf), ("videos", args.video), ("screenshots", args.screenshot)):
        for value in values:
            path = regular_file(value)
            if name == "documents":
                with path.open("rb") as stream:
                    if stream.read(5) != b"%PDF-":
                        raise ReleaseError(f"PDF input is not a PDF: {path.name}")
            if name == "screenshots" and path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".svg"}:
                raise ReleaseError(f"Screenshot input has an unsupported type: {path.name}")
            check_content(path, secrets=secrets, evidence=True)
            add_file(files, f"{name}/{path.name}", path)
    if args.evidence_root:
        root = Path(args.evidence_root).absolute()
        if not root.is_dir() or root.is_symlink():
            raise ReleaseError(f"Expected an evidence directory: {root}")
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = sorted(d for d in directories if d.lower() not in FORBIDDEN_DIRECTORIES and not (Path(current) / d).is_symlink())
            for name in sorted(names):
                path = Path(current) / name
                relative = path.relative_to(root).as_posix()
                reason = exclusion(relative)
                log_exempt = path.suffix.lower() == ".log" and exclusion(
                    str(PurePosixPath(relative).with_suffix(".txt"))
                ) is None
                if (reason and not log_exempt) or path.is_symlink():
                    continue
                path = regular_file(path)
                check_content(path, secrets=secrets, evidence=True)
                add_file(files, "evidence/" + relative, path)
    if args.asset_manifest:
        path = regular_file(args.asset_manifest)
        check_content(path, secrets=secrets, evidence=True)
        add_file(files, "assets/manifest.json", path)
    return files


def external_files(values: list[str], kind: str) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for value in values:
        path = regular_file(value)
        suffixes = {".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth"} if kind == "models" else {".tar", ".tgz", ".gz", ".zst"}
        if path.suffix.lower() not in suffixes:
            raise ReleaseError(f"Unsupported {kind} file type: {path.name}")
        add_file(files, f"{kind}/{path.name}", path)
    return files


def companion_files(values: list[str], *, kind: str, content_kind: str,
                    assets: dict[str, Path], secrets: list[bytes]) -> dict[str, Path]:
    """Include explicit plain-text licenses or exact image manifests, never env/key files."""
    if values and not assets:
        raise ReleaseError(f"{content_kind} inputs require corresponding {kind} assets")
    files: dict[str, Path] = {}
    for value in values:
        path = regular_file(value)
        if path.stat().st_size > MAX_COMPANION_FILE_BYTES:
            raise ReleaseError(f"Companion file exceeds 1 MiB: {path.name}")
        if content_kind == "license":
            if path.suffix.lower() not in {"", ".txt"} or not LICENSE_NAME.search(path.stem):
                raise ReleaseError(f"License must have a LICENSE/LICENCE filename and plain-text type: {path.name}")
        elif content_kind == "manifest":
            if path.suffix.lower() != ".json":
                raise ReleaseError(f"Image manifest must be a JSON file: {path.name}")
        else:
            raise ReleaseError("Unsupported companion content type")
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ReleaseError(f"Companion file is not UTF-8 text: {path.name}") from exc
        if any(ord(character) < 32 and character not in "\n\r\t" for character in content):
            raise ReleaseError(f"Companion file contains non-text data: {path.name}")
        check_content(path, secrets=secrets, evidence=True)
        for line in content.splitlines():
            match = TEXT_ASSIGNMENT.match(line)
            if match and SENSITIVE_KEY.search(match.group(1)):
                raw = match.group(2).strip().strip("\"'")
                if raw and not _template_value(raw):
                    raise ReleaseError(f"Credential assignment detected in companion file: {path.name}")
        if content_kind == "license":
            if not any(marker in content for marker in ("Apache License", "Permission", "Redistribution", "GNU GENERAL PUBLIC LICENSE")):
                raise ReleaseError(f"Companion file does not contain recognizable license text: {path.name}")
            folder = "licenses"
        else:
            data = json.loads(content)
            if not isinstance(data, dict) or not isinstance(data.get("images"), list) or not isinstance(data.get("archive_config_digests"), list):
                raise ReleaseError(f"Image manifest lacks checked image/config identity records: {path.name}")
            archive_name = Path(str(data.get("archive", ""))).name
            archive = assets.get(f"{kind}/{archive_name}")
            if archive is None or not isinstance(data.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", data["sha256"]):
                raise ReleaseError(f"Image manifest must refer to a supplied image archive: {path.name}")
            if archive.stat().st_size != data.get("size_bytes") or digest_file(archive) != data["sha256"]:
                raise ReleaseError(f"Image archive differs from its companion manifest: {path.name}")
            for item in data["images"]:
                if not isinstance(item, dict) or not isinstance(item.get("reference"), str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("image_id", ""))):
                    raise ReleaseError(f"Image manifest contains an invalid image identity: {path.name}")
            folder = "manifests"
        add_file(files, f"{kind}/{folder}/{path.name}", path)
    return files


def zip_info(name: str, mode: int, timestamp: datetime) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(safe_member(name), date_time=tuple(timestamp.timetuple())[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def create_archive(path: Path, prefix: str, files: dict[str, Path], timestamp: datetime, *, stored: bool = False) -> dict:
    manifest = {"schema_version": "1.0", "archive_root": prefix, "created_at": timestamp.isoformat(), "files": []}
    compression = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "x", compression=compression, compresslevel=None if stored else 6, allowZip64=True) as archive:
        for relative, source in sorted(files.items()):
            before = source.stat()
            mode = 0o755 if source.suffix == ".sh" else 0o644
            info = zip_info(f"{prefix}/{relative}", mode, timestamp)
            info.compress_type = compression
            digest = hashlib.sha256()
            with source.open("rb") as incoming, archive.open(info, "w", force_zip64=True) as outgoing:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    digest.update(chunk)
                    outgoing.write(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ReleaseError(f"Source changed during packaging; retry after edits finish: {relative}")
            manifest["files"].append({"path": relative, "size_bytes": before.st_size, "sha256": digest.hexdigest(), "mode": oct(mode)})
        body = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
        archive.writestr(zip_info(f"{prefix}/manifest.json", 0o644, timestamp), body)
        checksum = "".join(f"{item['sha256']}  {item['path']}\n" for item in manifest["files"])
        checksum += f"{hashlib.sha256(body).hexdigest()}  manifest.json\n"
        archive.writestr(zip_info(f"{prefix}/SHA256SUMS", 0o644, timestamp), checksum.encode())
    verified = verify_archive(path, require_source=prefix == "LayerTrace-source")
    return {"file": path.name, "size_bytes": path.stat().st_size, "sha256": digest_file(path), "entries": verified["entries"]}


def verify_archive(path: Path, *, require_source: bool = False) -> dict:
    with zipfile.ZipFile(path) as archive:
        names = []
        for info in archive.infolist():
            safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16) or info.is_dir():
                raise ReleaseError(f"Archive contains an unsupported link/directory member: {info.filename}")
            names.append(info.filename)
        if len(set(names)) != len(names):
            raise ReleaseError(f"Duplicate members in {path.name}")
        if len({name.casefold() for name in names}) != len(names):
            raise ReleaseError(f"Case-colliding members in {path.name}")
        roots = {name.split("/")[0] for name in names}
        if len(roots) != 1:
            raise ReleaseError(f"Archive must have exactly one root: {path.name}")
        prefix = next(iter(roots))
        manifest_name = f"{prefix}/manifest.json"
        if manifest_name not in names or f"{prefix}/SHA256SUMS" not in names:
            raise ReleaseError(f"Archive is missing its manifest/checksums: {path.name}")
        manifest = json.loads(archive.read(manifest_name))
        if manifest.get("archive_root") != prefix:
            raise ReleaseError(f"Archive root differs from manifest: {path.name}")
        expected = {manifest_name, f"{prefix}/SHA256SUMS"}
        records = manifest.get("files", [])
        if not isinstance(records, list):
            raise ReleaseError(f"Malformed file manifest: {path.name}")
        relative_names = set()
        for record in records:
            relative = safe_member(record["path"])
            if relative in relative_names:
                raise ReleaseError(f"Duplicate manifest path: {relative}")
            relative_names.add(relative)
            member = f"{prefix}/{relative}"
            expected.add(member)
            if member not in names:
                raise ReleaseError(f"Manifest member missing: {member}")
            digest = hashlib.sha256()
            size = 0
            with archive.open(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if size != record["size_bytes"] or digest.hexdigest() != record["sha256"]:
                raise ReleaseError(f"File hash/size mismatch: {member}")
        if expected != set(names):
            raise ReleaseError(f"Unlisted archive members in {path.name}")
        checksum = "".join(f"{item['sha256']}  {item['path']}\n" for item in records)
        checksum += f"{hashlib.sha256(archive.read(manifest_name)).hexdigest()}  manifest.json\n"
        if archive.read(f"{prefix}/SHA256SUMS").decode() != checksum:
            raise ReleaseError(f"Internal SHA256SUMS mismatch: {path.name}")
        if require_source:
            missing = sorted(set(REQUIRED_SOURCE) - relative_names)
            if missing:
                raise ReleaseError("Missing core assets in source archive: " + ", ".join(missing))
            forbidden = [name for name in relative_names if exclusion(name)]
            if forbidden:
                raise ReleaseError("Excluded local files leaked into source archive: " + ", ".join(forbidden[:10]))
        return {"file": path.name, "archive_root": prefix, "entries": len(records), "verified": True}


def create_bundle(source_zip: Path, target: Path, timestamp: datetime) -> dict:
    git = shutil.which("git")
    if not git:
        raise ReleaseError("Git is required only for --git-bundle")
    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_DATE": timestamp.isoformat(), "GIT_COMMITTER_DATE": timestamp.isoformat()})
    with tempfile.TemporaryDirectory(prefix="layertrace-release-git-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(source_zip) as archive:
            for info in archive.infolist():
                relative = safe_member(info.filename).split("/", 1)[1]
                output = root / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as incoming, output.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                output.chmod((info.external_attr >> 16) & 0o777)

        def run(*arguments: str) -> str:
            result = subprocess.run([git, "-c", "init.templateDir=", "-c", "core.hooksPath=" + os.devnull, *arguments], cwd=root, env=env, text=True, capture_output=True)
            if result.returncode:
                raise ReleaseError("Isolated Git snapshot failed: " + result.stderr.strip())
            return result.stdout.strip()

        run("init", "-b", "submission")
        run("add", "--all", "--force")
        run("-c", "user.name=LayerTrace Release", "-c", "user.email=release@localhost", "-c", "commit.gpgSign=false", "commit", "--no-gpg-sign", "-m", "LayerTrace delivery snapshot")
        revision = run("rev-parse", "HEAD")
        run("bundle", "create", str(target.absolute()), "HEAD", "refs/heads/submission")
        run("bundle", "verify", str(target.absolute()))
    return {"file": target.name, "size_bytes": target.stat().st_size, "sha256": digest_file(target), "revision": revision, "kind": "isolated_sanitized_snapshot"}


def validate_video(path: Path) -> dict:
    executable = shutil.which("ffprobe")
    if not executable:
        return {"file": path.name, "duration_verified": False, "reason": "ffprobe unavailable"}
    result = subprocess.run([executable, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)], text=True, capture_output=True)
    if result.returncode:
        raise ReleaseError(f"Video cannot be probed: {path.name}")
    content = json.loads(result.stdout)
    duration = float(content.get("format", {}).get("duration", 0))
    has_video = any(item.get("codec_type") == "video" for item in content.get("streams", []))
    return {"file": path.name, "duration_seconds": duration, "duration_verified": has_video and 180 <= duration <= 300}


def create_release(args) -> dict:
    source = Path(args.source_root).absolute()
    output = Path(args.output).absolute()
    if not source.is_dir() or output.is_symlink():
        raise ReleaseError("Source must be a directory and output must not be a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).replace(microsecond=0)
    secrets = load_known_secrets(source, args.secret_file)
    sources, exclusions = source_files(source, secrets, final=args.require_submission)
    materials = material_files(args, secrets)
    models = external_files(args.model_file, "models")
    images = external_files(args.image_file, "offline-images")
    models.update(companion_files(args.model_license, kind="models", content_kind="license", assets=models, secrets=secrets))
    images.update(companion_files(args.image_license, kind="offline-images", content_kind="license", assets=images, secrets=secrets))
    images.update(companion_files(args.image_manifest, kind="offline-images", content_kind="manifest", assets=images, secrets=secrets))
    videos = [validate_video(regular_file(path)) for path in args.video]
    gates = {
        "source_complete": True,
        "pdf_present": bool(args.pdf),
        "video_3_to_5_minutes": bool(videos) and all(video["duration_verified"] for video in videos),
        "environment_evidence_present": "evidence/environment.json" in materials,
        "scenario_evidence_present": any(name.startswith("evidence/runs/") or name.startswith("evidence/lab/") for name in materials),
        "asset_manifest_present": bool(args.asset_manifest),
    }
    if args.require_submission and not all(gates.values()):
        raise ReleaseError("Incomplete local submission materials: " + ", ".join(key for key, passed in gates.items() if not passed))
    names = ["LayerTrace-source.zip", "release-manifest.json", "SHA256SUMS"]
    if materials:
        names.append("LayerTrace-materials.zip")
    if models:
        names.append("LayerTrace-models.zip")
    if images:
        names.append("LayerTrace-offline-images.zip")
    if args.git_bundle:
        names.append("LayerTrace-source.bundle")
    existing = [name for name in names if (output / name).exists()]
    if existing:
        raise ReleaseError("Output already contains delivery artifacts; choose a fresh directory: " + ", ".join(existing))

    with tempfile.TemporaryDirectory(prefix=".layertrace-package-", dir=output) as temporary:
        stage = Path(temporary)
        artifacts = [create_archive(stage / "LayerTrace-source.zip", "LayerTrace-source", sources, timestamp)]
        for name, prefix, files in (("LayerTrace-materials.zip", "LayerTrace-materials", materials), ("LayerTrace-models.zip", "LayerTrace-models", models), ("LayerTrace-offline-images.zip", "LayerTrace-offline-images", images)):
            if files:
                artifacts.append(create_archive(stage / name, prefix, files, timestamp, stored=prefix in {"LayerTrace-models", "LayerTrace-offline-images"}))
        if args.git_bundle:
            artifacts.append(create_bundle(stage / "LayerTrace-source.zip", stage / "LayerTrace-source.bundle", timestamp))
        release = {"schema_version": "1.0", "created_at": timestamp.isoformat(), "source_root": str(source), "artifacts": artifacts, "local_material_gates": gates, "local_materials_complete": all(gates.values()), "repository_published": False, "video_published": False, "mail_sent": False, "videos": videos, "excluded_source_files": exclusions}
        (stage / "release-manifest.json").write_text(json.dumps(release, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        checksum_names = [item["file"] for item in artifacts] + ["release-manifest.json"]
        (stage / "SHA256SUMS").write_text("".join(f"{digest_file(stage / name)}  {name}\n" for name in checksum_names), encoding="utf-8")
        for name in names:
            (stage / name).replace(output / name)
    verify_release(output)
    return {"output": str(output), "artifacts": artifacts, "local_materials_complete": all(gates.values())}


def verify_release(root: Path) -> dict:
    root = root.absolute()
    manifest_path = regular_file(root / "release-manifest.json")
    release = json.loads(manifest_path.read_text(encoding="utf-8"))
    names = [item["file"] for item in release["artifacts"]] + ["release-manifest.json"]
    expected = ""
    results = []
    for name in names:
        if safe_member(name) != name or "/" in name:
            raise ReleaseError(f"Invalid artifact path: {name}")
        path = regular_file(root / name)
        digest = digest_file(path)
        expected += f"{digest}  {name}\n"
        if name != "release-manifest.json":
            record = next(item for item in release["artifacts"] if item["file"] == name)
            if path.stat().st_size != record["size_bytes"] or digest != record["sha256"]:
                raise ReleaseError(f"Artifact hash/size mismatch: {name}")
            if path.suffix == ".zip":
                results.append(verify_archive(path, require_source=name == "LayerTrace-source.zip"))
    if (root / "SHA256SUMS").read_text(encoding="utf-8") != expected:
        raise ReleaseError("External SHA256SUMS mismatch")
    return {"output": str(root), "verified": True, "archives": results, "local_materials_complete": release["local_materials_complete"]}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="Create isolated source/material/model/image packages")
    create.add_argument("--source-root", default=str(Path(__file__).resolve().parents[1]))
    create.add_argument("--output", default="/mnt/d/ZSvirt/LayerTrace-delivery")
    create.add_argument("--evidence-root", help="Explicit, already redacted evidence directory")
    create.add_argument("--pdf", action="append", default=[], help="Final PDF; repeat for multiple documents")
    create.add_argument("--video", action="append", default=[], help="Actual demonstration video")
    create.add_argument("--screenshot", action="append", default=[], help="Explicit final screenshot allowlist")
    create.add_argument("--asset-manifest", help="Final asset provenance/version/SHA256/license JSON")
    create.add_argument("--model-file", action="append", default=[], help="Model weights for a separate asset archive")
    create.add_argument("--image-file", action="append", default=[], help="Optional docker-save tar for separate offline images")
    create.add_argument("--model-license", action="append", default=[], help="Original UTF-8 LICENSE/LICENCE text accompanying model assets")
    create.add_argument("--image-license", action="append", default=[], help="Original UTF-8 LICENSE/LICENCE text accompanying image assets")
    create.add_argument("--image-manifest", action="append", default=[], help="Exact checked JSON image/config manifest matching a supplied image archive")
    create.add_argument("--secret-file", action="append", default=[], help="Local env file used only for literal credential leak detection")
    create.add_argument("--git-bundle", action="store_true", help="Create a Git bundle from the exact sanitized source ZIP")
    create.add_argument("--require-submission", action="store_true", help="Reject missing PDF/video/evidence/assets and unfilled delivery records")
    verify = sub.add_parser("verify", help="Verify an existing release directory")
    verify.add_argument("path")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        result = create_release(args) if args.command == "create" else verify_release(Path(args.path))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ReleaseError, OSError, KeyError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Release validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
