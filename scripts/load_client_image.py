#!/usr/bin/env python3
"""Load and verify the independently packaged LayerTrace client image.

This imports image data and checks its named image; it never starts services.
Use --check-archive-only to verify supplied files without accessing Docker.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--check-archive-only", action="store_true")
    args = parser.parse_args()
    for path in (args.archive, args.manifest):
        if not path.is_file() or path.is_symlink():
            raise SystemExit("Expected a regular archive and manifest")
    if args.manifest.stat().st_size > 1024 * 1024:
        raise SystemExit("Client manifest exceeds its size limit")
    data = json.loads(args.manifest.read_text(encoding="utf-8"))
    images = data.get("images")
    configurations = data.get("archive_config_digests")
    if not isinstance(images, list) or len(images) != 1 or not isinstance(configurations, list):
        raise SystemExit("Expected one independent client image and checked configuration records")
    image = images[0]
    reference = image.get("reference", "")
    image_id = image.get("image_id", "")
    if not re.fullmatch(r"layertrace-demo-client:[a-zA-Z0-9_.-]+", reference):
        raise SystemExit("Client manifest has an unexpected image reference")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
        raise SystemExit("Client manifest has an invalid image identity")
    configs = set()
    for row in configurations:
        if reference in row.get("tags", []):
            value = str(row.get("config_digest", ""))
            value = value if value.startswith("sha256:") else "sha256:" + value
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise SystemExit("Client manifest has an invalid configuration digest")
            configs.add(value)
    if len(configs) != 1:
        raise SystemExit("Expected one checked archive configuration for the client")
    if (Path(str(data.get("archive", ""))).name != args.archive.name
            or args.archive.stat().st_size != data.get("size_bytes")
            or digest_file(args.archive) != data.get("sha256")):
        raise SystemExit("Client archive differs from its supplied manifest")
    if args.check_archive_only:
        print("Client archive size and SHA-256 match; Docker was not accessed.")
        return 0
    subprocess.run(["docker", "load", "--input", str(args.archive)], check=True)
    result = json.loads(subprocess.check_output(["docker", "image", "inspect", reference]))[0]
    if result["Id"] != image_id or image_id not in data.get("archive_image_digests", []):
        raise SystemExit("Loaded client raw image identity differs from the checked archive index identity")
    compose_reference = data.get("compose_reference", "")
    if compose_reference != image_id:
        raise SystemExit("The supplied client Compose override requires its checked raw image identity")
    compose_image = json.loads(subprocess.check_output(["docker", "image", "inspect", compose_reference]))[0]
    if compose_image["Id"] != image_id:
        raise SystemExit("The loaded store cannot resolve the supplied client Compose reference")
    print(f"Verified independent client {reference}: {result['Id']}")
    print("No service was started; use the supplied client Compose override.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
