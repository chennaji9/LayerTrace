#!/usr/bin/env bash
set -euo pipefail
task_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
task_env="${1:-$task_root/deploy/vm-probe/.env}"
if [[ ! -f "$task_env" ]]; then
  echo "Create $task_env from deploy/vm-probe/env.example with VM UUID and credentials." >&2
  exit 2
fi
chmod 600 "$task_env"
mkdir -p "$task_root/deploy/vm-probe/certs"
docker compose --project-name layertrace-vm --env-file "$task_env" -f "$task_root/deploy/vm-probe/compose.yaml" -f "$task_root/deploy/workload/compose.yaml" config --quiet
docker compose --project-name layertrace-vm --env-file "$task_env" -f "$task_root/deploy/vm-probe/compose.yaml" -f "$task_root/deploy/workload/compose.yaml" up -d --build
docker compose --project-name layertrace-vm --env-file "$task_env" -f "$task_root/deploy/vm-probe/compose.yaml" -f "$task_root/deploy/workload/compose.yaml" ps
