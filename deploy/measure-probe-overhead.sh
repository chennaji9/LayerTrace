#!/usr/bin/env bash
# Run on the actual Linux business VM with sudo; inspect an existing load only.
set -euo pipefail
task_script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$task_script_dir/local-lab/measure.py" "$@"
