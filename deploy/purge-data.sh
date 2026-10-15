#!/usr/bin/env bash
# 独立数据删除：必须显式给出精确卷名，验证Compose项目标签后再确认。
set -euo pipefail

log() { printf '[purge-data] %s\n' "$*"; }

task_project=""
if [[ "${1:-}" == "--project-name" ]]; then
  [[ $# -ge 3 ]] || { echo "用法：$0 [--project-name 项目名] 精确卷名 [精确卷名...]" >&2; exit 2; }
  task_project="$2"
  shift 2
fi
[[ $# -gt 0 ]] || { echo "请先核对卷与备份。用法：$0 [--project-name 项目名] 精确卷名 [精确卷名...]" >&2; exit 2; }
[[ -z "$task_project" || "$task_project" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { echo "项目名格式无效" >&2; exit 2; }

task_volumes=()
declare -A task_seen=()
for task_volume in "$@"; do
  [[ "$task_volume" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]] || { echo "卷名格式无效" >&2; exit 2; }
  [[ -z "${task_seen[$task_volume]:-}" ]] || { echo "重复卷名，未执行删除：$task_volume" >&2; exit 2; }
  task_seen[$task_volume]=true
  task_actual_name="$(docker volume inspect --format '{{.Name}}' "$task_volume")"
  [[ "$task_actual_name" == "$task_volume" ]] || { echo "卷身份不匹配，未执行删除" >&2; exit 1; }
  task_actual_project="$(docker volume inspect --format '{{index .Labels "com.docker.compose.project"}}' "$task_volume")"
  if [[ -n "$task_project" ]]; then
    [[ "$task_actual_project" == "$task_project" ]] || { echo "卷不属于指定项目，未执行删除：$task_volume" >&2; exit 1; }
  else
    [[ "$task_actual_project" == layertrace || "$task_actual_project" == layertrace-vm ]] || { echo "卷不属于默认项目，需显式指定实际project-name：$task_volume" >&2; exit 1; }
  fi
  task_volumes+=("$task_volume")
  log "已核对：$task_volume（项目：$task_actual_project）"
done

log "仅删除上述精确卷，数据不可恢复。停止使用这些卷的服务，并确认已有必要备份。"
read -r -p "确认删除这些卷？输入 YES 继续：" answer
[[ "$answer" == "YES" ]] || { log "已取消"; exit 1; }

for task_volume in "${task_volumes[@]}"; do
  docker volume rm -- "$task_volume"
  log "已删除 $task_volume"
done

log "数据删除完成。"
