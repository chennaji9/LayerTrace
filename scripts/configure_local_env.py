#!/usr/bin/env python3
"""Create a protected local .env with strong random credentials; never print secrets."""

from __future__ import annotations

import argparse
import os
import re
import secrets
from pathlib import Path


def create_env(target: Path, template: Path, *, mode: str = "prod") -> Path:
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Configuration already exists and was preserved: {target}")
    database_password = secrets.token_hex(24)
    replacements = {
        "MODE": mode,
        "LAB_ENABLED": "false",
        "POSTGRES_PASSWORD": database_password,
        "DATABASE_URL": f"postgresql+asyncpg://layertrace:{database_password}@postgres:5432/layertrace",
        "GRAFANA_ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "API_TOKENS": secrets.token_urlsafe(32),
        "PROBE_REGISTRATION_SECRET": secrets.token_urlsafe(32),
        "TRUSTED_PROXIES": "172.31.255.0/24",
        "PLAINTEXT_CLIENT_CIDRS": "172.31.255.1/32",
    }
    text = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        if re.search(rf"^{re.escape(key)}=", text, flags=re.M):
            text = re.sub(rf"^{re.escape(key)}=.*$", f"{key}={value}", text, flags=re.M)
        else:
            text += f"\n{key}={value}\n"
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    target.chmod(0o600)
    return target


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=root / ".env")
    parser.add_argument("--template", type=Path, default=root / ".env.example")
    parser.add_argument("--mode", choices=("prod", "lab", "replay"), default="prod")
    args = parser.parse_args()
    try:
        target = create_env(args.path.absolute(), args.template, mode=args.mode)
    except (FileExistsError, OSError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Created protected local configuration: {target}")
    print("Credentials are stored only in that file. Review deployment addresses before starting services.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
