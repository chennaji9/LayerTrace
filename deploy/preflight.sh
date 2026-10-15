#!/usr/bin/env bash
# LayerTrace 只读环境预检。
# - 只做读取与探测，不修改环境、不注入任何内容。
# - --output 指定环境风险记录文件（默认 docs/environment-risk.md），仅更新
#   "预检执行记录" 自动章节；ZSvirt 实测字段必须人工核实后填写，脚本不会伪造。

set -euo pipefail

OUTPUT_FILE="docs/environment-risk.md"

log() { printf '[preflight] %s\n' "$*"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT_FILE="$2"; shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

log "只读环境预检开始"
log "输出文件: $OUTPUT_FILE"

command -v python3 >/dev/null 2>&1 || { echo "错误：需要 python3" >&2; exit 1; }
if ! command -v docker >/dev/null 2>&1; then
  log "警告：未找到 docker（部署与集成测试需要）"
fi

python3 - "$OUTPUT_FILE" <<'PYEOF'
import datetime
import json
import os
import pathlib
import re
import subprocess
import sys

target = pathlib.Path(sys.argv[1])
begin, end = "<!-- preflight:begin:auto -->", "<!-- preflight:end:auto -->"

if not target.exists():
    print(f"错误：目标文件 {target} 不存在（模板需先提交）", file=sys.stderr)
    sys.exit(1)


def sh(cmd: str, default: str = "UNAVAILABLE") -> str:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else default
    except Exception:
        return default


checks = {
    "timestamp_utc": datetime.datetime.now(datetime.UTC).isoformat(),
    "docker": sh("docker --version"),
    "docker_daemon": "ok" if sh("docker info >/dev/null 2>&1 && echo ok") == "ok" else "unavailable",
    "compose": sh("docker compose version"),
    "os": sh("uname -a"),
    "is_wsl": "yes" if "microsoft" in sh("uname -r").lower() else "no",
    "cpu_count": os.cpu_count(),
    "memory_mib": int(mem) if (mem := sh("free -m | awk '/^Mem:/{print $2}'")).isdigit() else None,
    "disk_free_gib": sh("df -BG --output=avail . | tail -1 | tr -dc '0-9'") or None,
    "loopback_port_8000_listeners": sh("ss -ltn 2>/dev/null | grep -c ':8000 '"),
    "cert_dir_present": "yes" if pathlib.Path("deploy/caddy/certs").exists() else "no",
}

body = (
    begin
    + "\n\n## 预检执行记录（自动生成，仅环境事实）\n\n```json\n"
    + json.dumps(checks, ensure_ascii=False, indent=2)
    + "\n```\n\n"
    + end
)
text = target.read_text(encoding="utf-8")
if begin in text and end in text:
    text = re.sub(re.escape(begin) + r".*?" + re.escape(end), lambda _: body, text, flags=re.S)
else:
    text = text.rstrip() + "\n\n" + body + "\n"
target.write_text(text, encoding="utf-8")
print("[preflight] 已写入自动章节 ->", target)
PYEOF

log "完成。注意：ZSvirt 字段、网络方向与权限结论必须人工实测后填写，脚本不生成。"
