#!/usr/bin/env bash
# LayerTrace 安装脚本：依赖检查、随机本地凭据配置、控制面服务启动。
# 可重复执行（幂等）。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

log() { printf '[install] %s\n' "$*"; }

# 1) 依赖检查
command -v docker >/dev/null || { echo "错误：需要 docker" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "错误：需要 docker compose 插件" >&2; exit 1; }
command -v python3 >/dev/null || { echo "错误：需要 python3" >&2; exit 1; }

# 2) 配置安装（幂等：不覆盖已存在的 .env）
if [[ ! -f .env ]]; then
  python3 scripts/configure_local_env.py --mode "${MODE:-prod}"
  log "已创建受保护的本地 .env，密码与令牌随机生成；请核对实际平台地址"
else
  log ".env 已存在，跳过"
fi

# 3) 证书目录（不入仓库）
mkdir -p deploy/caddy/certs
if [[ ! -f deploy/caddy/certs/tls.crt ]]; then
  log "提示：跨主机入口需要证书（见 deploy/caddy/README.md）；loopback 演示可跳过"
fi

# 4) PROBE_REGISTRATION_SECRET 为注册凭证；PROBE_REGISTRATION_TOKEN 仅是兼容变量。
# 缺少密码、API令牌或注册secret时，Compose校验拒绝启动且不显示凭据。
docker compose -f deploy/compose.yaml config --quiet

# 5) 服务启动（基础控制面；可选链路组件通过 standard profile 部署）
services=(control-plane worker postgres prometheus grafana)
if [[ -f deploy/caddy/certs/tls.crt && -f deploy/caddy/certs/tls.key ]]; then
  services+=(caddy)
fi
log "启动 ${services[*]}"
docker compose -f deploy/compose.yaml up -d --build "${services[@]}"

log "完成。浏览器打开 http://localhost:8000 并使用本地 .env 中的访问令牌登录"
