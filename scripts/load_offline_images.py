#!/usr/bin/env python3
"""Verify the supplied offline image archive and load its exact named images.

This command imports image data; it does not start or restart services.
A manifest may select its own load_references; older manifests keep their
original four named references.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

LEGACY_REFERENCES = (
    "layertrace-control-plane:local",
    "layertrace-control-plane:delivery-20261015-final",
    "postgres:16.4-alpine3.20",
    "prom/prometheus:v3.5.0",
)


def verify_archive(archive_path: Path, manifest: dict) -> dict[str, set[str]]:
    if not archive_path.is_file() or archive_path.is_symlink():
        raise ValueError("Archive must be a regular file")
    digest = hashlib.sha256()
    with archive_path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            digest.update(chunk)
    if archive_path.stat().st_size != manifest["size_bytes"] or digest.hexdigest() != manifest["sha256"]:
        raise ValueError("Archive size or SHA-256 does not match the supplied manifest")
    references = manifest.get("load_references", list(LEGACY_REFERENCES))
    if (
        not isinstance(references, list)
        or not references
        or any(not isinstance(value, str) or not value for value in references)
        or len(set(references)) != len(references)
    ):
        raise ValueError("Manifest load_references must be a nonempty list of unique names")
    expected = {image["reference"]: image for image in manifest["images"]}
    configs = {
        tag: row["config_digest"].removeprefix("sha256:")
        for row in manifest["archive_config_digests"]
        for tag in row.get("tags") or []
    }
    verified = {}
    with tarfile.open(archive_path) as archive:
        stream = archive.extractfile("manifest.json")
        if stream is None:
            raise ValueError("Archive has no Docker manifest")
        archive_manifest = json.load(stream)
        archive_configs = {
            tag: row["Config"]
            for row in archive_manifest
            for tag in row.get("RepoTags") or []
        }
        for reference in references:
            if reference not in expected or reference not in configs or reference not in archive_configs:
                raise ValueError(f"Selected image is absent from manifest or archive: {reference}")
            config = archive.extractfile(archive_configs[reference])
            if config is None or hashlib.sha256(config.read()).hexdigest() != configs[reference]:
                raise ValueError(f"Archive config identity mismatch: {reference}")
            image_id = expected[reference]["image_id"]
            if not isinstance(image_id, str) or not image_id.startswith("sha256:"):
                raise ValueError(f"Invalid expected image identity: {reference}")
            verified[reference] = {image_id, "sha256:" + configs[reference]}
    return verified


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--check-archive-only", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    expected = verify_archive(args.archive, manifest)
    if args.check_archive_only:
        print("Offline archive and selected image config identities verified; Docker was not accessed.")
        return 0
    subprocess.run(["docker", "load", "--input", str(args.archive)], check=True)
    for reference, identities in expected.items():
        data = json.loads(subprocess.check_output(["docker", "image", "inspect", reference]))[0]
        # Containerd reports the OCI index ID; Docker's classic store reports
        # the platform config ID. Both are taken from the checked archive.
        if data["Id"] not in identities:
            raise SystemExit(f"Loaded image identity mismatch: {reference}")
        print(f"Verified {reference}: {data['Id']}")
    print("Offline images loaded and verified; services can now start with --pull never.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
