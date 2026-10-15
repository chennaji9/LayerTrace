#!/usr/bin/env bash
set -euo pipefail
task_assets="${1:-/opt/layertrace-assets}"
task_name=qwen2.5-0.5b-instruct-q4_k_m.gguf
task_sha=74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db
mkdir -p "$task_assets"
task_path="$task_assets/$task_name"
if [[ -f "$task_path" ]] && printf '%s  %s\n' "$task_sha" "$task_path" | sha256sum --check --status; then
  echo "Verified model: $task_path"
  exit 0
fi
task_source="${MODEL_DOWNLOAD_BASE:-https://huggingface.co}/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/$task_name"
curl --fail --location --continue-at - --retry 5 --connect-timeout 20 --max-time 1800 --output "$task_path" "$task_source"
printf '%s  %s\n' "$task_sha" "$task_path" | sha256sum --check
curl --fail --location --retry 3 --output "$task_assets/Qwen2.5-LICENSE.txt" "${MODEL_DOWNLOAD_BASE:-https://huggingface.co}/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/LICENSE"
echo "Model and license saved to $task_assets"
