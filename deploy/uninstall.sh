#!/usr/bin/env bash
# LayerTrace 卸载：先服务器撤销，再 docker compose down，保留数据卷。
# 数据删除是独立显式操作 deploy/purge-data.sh。
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$task_root"
exec python3 "$task_root/scripts/uninstall.py" "$@"
