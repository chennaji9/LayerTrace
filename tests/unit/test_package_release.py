"""Release safety gates cover traversal, credentials, completeness and mutation."""

from __future__ import annotations

import json
import zipfile
from argparse import Namespace
from datetime import UTC, datetime

import pytest

from scripts.package_release import (
    REQUIRED_SOURCE,
    ReleaseError,
    check_content,
    create_archive,
    exclusion,
    material_files,
    safe_member,
    verify_archive,
)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "safe/../escape", "C:/escape", "safe\\escape", "safe//file", "safe/./file"])
def test_reject_traversal_and_platform_paths(name):
    with pytest.raises(ReleaseError, match="Unsafe"):
        safe_member(name)


@pytest.mark.parametrize("name", ["deploy/vm-probe/.env", "apps/__pycache__/x.pyc", "deploy/caddy/certs/server.key", "runtime/events.json", "deploy/models/weights.gguf", "docs/screenshots/demo-01-overview.png"])
def test_source_excludes_local_state_and_large_assets(name):
    assert exclusion(name)


def test_release_archive_hashes_and_core_files_are_verified(tmp_path):
    files = {}
    for name in REQUIRED_SOURCE:
        path = tmp_path / "input" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source " + name, encoding="utf-8")
        files[name] = path
    archive_path = tmp_path / "source.zip"
    result = create_archive(archive_path, "LayerTrace-source", files, datetime(2026, 10, 14, tzinfo=UTC))
    assert result["entries"] == len(REQUIRED_SOURCE)
    assert verify_archive(archive_path, require_source=True)["verified"]
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(archive_path) as original, zipfile.ZipFile(broken, "w") as modified:
        for item in original.infolist():
            modified.writestr(item, b"tampered" if item.filename.endswith("README.md") else original.read(item.filename))
    with pytest.raises(ReleaseError, match="hash/size mismatch"):
        verify_archive(broken, require_source=True)


def test_missing_core_assets_and_duplicate_members_are_rejected(tmp_path):
    source = tmp_path / "README.md"
    source.write_text("demo")
    with pytest.raises(ReleaseError, match="Missing core assets"):
        create_archive(tmp_path / "missing.zip", "LayerTrace-source", {"README.md": source}, datetime.now(UTC))
    archive = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("root/item", "one")
        with pytest.warns(UserWarning):
            output.writestr("root/item", "two")
    with pytest.raises(ReleaseError, match="Duplicate members"):
        verify_archive(archive)


def test_evidence_credentials_and_unfilled_delivery_records_are_rejected(tmp_path):
    evidence = tmp_path / "environment.json"
    evidence.write_text(json.dumps({"config": {"api_token": "private-actual-value"}}))
    with pytest.raises(ReleaseError, match="credential field"):
        check_content(evidence, secrets=[], evidence=True)
    evidence.write_text(json.dumps({"config": {"api_token": "[REDACTED]"}, "sha256": "0" * 64}))
    check_content(evidence, secrets=[], evidence=True)
    document = tmp_path / "summary.md"
    document.write_text("[实测回填：task ID]", encoding="utf-8")
    with pytest.raises(ReleaseError, match="Unfilled"):
        check_content(document, secrets=[], final=True)


def test_literal_configured_secret_detection_crosses_read_boundaries(tmp_path):
    path = tmp_path / "evidence.bin"
    secret = b"actual-configured-token"
    path.write_bytes(b"x" * (1024 * 1024 - 5) + secret)
    with pytest.raises(ReleaseError, match="Configured credential"):
        check_content(path, secrets=[secret], evidence=True)


def test_log_evidence_exception_keeps_credential_filename_exclusions(tmp_path):
    (tmp_path / "events.log").write_text("observed safe event")
    (tmp_path / "credentials.log").write_text("must be excluded")
    (tmp_path / "secret-registration.log").write_text("must be excluded")
    (tmp_path / ".env.private.log").write_text("must be excluded")
    args = Namespace(pdf=[], video=[], screenshot=[], evidence_root=str(tmp_path), asset_manifest=None)
    files = material_files(args, [])
    assert set(files) == {"evidence/events.log"}
