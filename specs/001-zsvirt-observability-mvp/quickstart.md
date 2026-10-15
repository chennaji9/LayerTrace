# LayerTrace 部署与验证指南

## 1. 范围与环境

控制面提供资源、任务、事件单和证据诊断。ZSvirt 平台只读采集与 VM 内探针共同关联实际资源。没有平台或 GPU 时可使用明确标记的回放路径，不能将回放称为真实故障实验。

控制面建议 Linux x86_64、4 vCPU、8 GiB 内存和 20 GiB 可用磁盘，需 Docker Engine、Compose v2、Python 3.11+。业务 VM 的探针与模型预算见 `deploy/vm-probe/README.md`，平台管理节点预算单独按 ZSvirt 官方要求准备。最终最低环境以实际部署报告为准。

所有命令在源码根目录执行。HTTP 入口默认只绑定本机 127.0.0.1，跨主机浏览器与探针使用受信 HTTPS。令牌、数据库口令、注册 secret、证书私钥与本地 .env 不进入源码或交付归档。

## 2. 创建本地受保护配置

首次部署运行：

```bash
python3 scripts/configure_local_env.py --mode replay
docker compose -f deploy/compose.yaml config --quiet
docker compose -f deploy/compose.yaml up -d --build control-plane worker postgres prometheus grafana
docker compose -f deploy/compose.yaml ps
```

配置生成器为数据库、Grafana、项目 API 与探针注册分别生成强随机值，将数据库密码同步到 DATABASE_URL，并设置文件权限为 0600。已存在的 .env 会保留，工具拒绝覆盖。Compose 对缺少密码或令牌的配置拒绝启动。实际平台部署将 MODE 设为 prod 或 lab，并填入真实 ZSvirt 连接配置；真实故障由业务 VM 的授权 CLI 执行。

浏览器打开 `http://127.0.0.1:8000/`，填写本地 .env 中的 API_TOKENS 访问令牌。不要将该值放在截图、视频或报告中。登录会话有效期 12 小时，写操作校验会话绑定的 CSRF。

API 客户端可从已授权配置在进程内读取令牌：

```bash
python3 - <<'PY'
from pathlib import Path
import json, urllib.request
values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines()
              if "=" in line and not line.lstrip().startswith("#"))
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/v1/status",
    headers={"Authorization": "Bearer " + values["API_TOKENS"].split(",")[0]},
)
with urllib.request.urlopen(request) as response:
    print(json.dumps(json.load(response), ensure_ascii=False, indent=2))
PY
```

## 3. 确认状态与指标

点击“状态”，核查 PostgreSQL、Prometheus、ZSvirt 和实际探针的状态、最后成功时间、积压与丢弃。未配置 ZSvirt 时应显示未配置，不能显示平台已连接。未解析身份非零时先修复绑定。

Prometheus 使用 `prom/prometheus:v3.5.0@sha256:63805ebb8d2b3920190daf1cb14a60871b16fd38bed42b857a3182bc621f4996`。抓取与规则评估间隔 10 秒，静态目标目录 `deploy/prometheus/targets` 已只读挂载。默认目标为空，真实 VM 上报后按 `deploy/prometheus/targets/README.md` 配置可访问的 9108 目标，并从 Prometheus 的 targets 接口确认抓取成功。空目标配置不能作为指标采集成功的证明。

跨主机 HTTPS 的证书、主机名、受信 CA 和 Caddy 配置见 `deploy/caddy/README.md`。准备证书后单独启动：

```bash
docker compose -f deploy/compose.yaml up -d caddy
```

## 4. 回放与反例验证

页面“实验”中的“回放演示”提供容器 OOM、退出码 137 反例、宿主资源压力和 CUDA OOM 回放。选择场景，点击“运行回放”和“验证回放”，再查看诊断。所有资源、事件和证据保留回放来源。

在控制面容器中使用 API 客户端需要显式令牌变量：

```bash
docker compose -f deploy/compose.yaml exec control-plane sh -c 'LAYERTRACE_TOKEN="$API_TOKENS" layertrace scenario run story-oom --mode replay'
docker compose -f deploy/compose.yaml exec control-plane sh -c 'LAYERTRACE_TOKEN="$API_TOKENS" layertrace scenario verify <run_id> --json'
```

将 `<run_id>` 替换为正式返回值；API_TOKENS 配置多个令牌时，只将其中一个赋给 LAYERTRACE_TOKEN。网页“验证回放”可避免手动处理令牌。

回放 OOM 应展示直接证据和反证，退出码 137 反例不应形成仅凭退出码的 OOM 高支持归因。回放的恢复入口只属于保存场景生命周期，不能替代真实容器恢复。

## 5. 真实平台与业务 VM

在控制面 .env 配置 `ZSVIRT_ENABLED=true`、实际 Base URL、只读 session/AccessKey 配置，以及 `ZSVIRT_PROVIDER_INSTANCE`。平台版本、接口、权限、告警类型和缺失项需记录在实际环境证据中。

按 `deploy/vm-probe/README.md` 在 ZSvirt 管理的业务 VM 部署探针、模型、Agent、工具和独立客户端。使用真实 VM UUID，探针安装 ID 单独生成，服务实例身份由探针发现。平台凭证保存在控制面，业务 VM 只持有注册 secret 与项目范围凭证。

```bash
bash deploy/workload/fetch-model.sh /opt/layertrace-assets
cp deploy/vm-probe/env.example deploy/vm-probe/.env
chmod 600 deploy/vm-probe/.env
# 在受保护文件中填写VM UUID、安装UUID、控制面URL、注册secret、项目令牌与CA
bash deploy/vm-probe/install.sh
```

必须将探针与工作负载的两个 Compose 文件作为同一项目合并执行。正常业务样本：

```bash
docker compose --project-name layertrace-vm --env-file deploy/vm-probe/.env \
  -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
  run --rm demo-client python -m workloads.demo_agent.load_generator --count 35 --interval 1
```

在任务页检查真实成功任务及模型、工具耗时；在工作负载页检查当前数据与健康基线。基线需要至少 30 个符合条件的正常样本，不足时保留缺失状态。

## 6. 三类真实实验与恢复

在业务 VM 源码目录运行，CLI 只操作带 `layertrace.lab=true` 标签的目标，保存完整容器 ID 与原配置，限时最长 180 秒：

```bash
python3 -m scenarios.real_lab run process-failure --target layertrace-demo-agent --seconds 45 --output evidence/lab
python3 -m scenarios.real_lab run cpu-quota --target layertrace-demo-model --cpus 0.10 --seconds 90 --output evidence/lab
python3 -m scenarios.real_lab run tool-503 --target layertrace-demo-tool --seconds 45 --output evidence/lab
```

每次只运行一种故障，维持正常业务请求流。记录 run/task/incident ID，在网页从任务详情进入关联诊断，复核具体值、来源、对象、时间、依赖、反证和缺失项。进程中断不是内核 OOM；CPU 利用率高本身不能证明配额瓶颈；工具与最终任务失败必须是同一 task ID。

CLI 自动恢复后检查配置和新业务请求。独立恢复命令：

```bash
python3 -m scenarios.real_lab restore --state evidence/lab/<run_id>/state.json
```

在事件单页确认事件、按指纹静默通知。全部相关告警恢复并满足稳定窗口后确认已恢复；默认稳定窗口 60 秒。采集陈旧、空档或静默不能替代恢复。

## 7. 可靠性、身份与权限

- 同名容器、不同 VM、不同任务、容器重建应保留身份边界。历史关系按故障时刻查询。
- 停止探针三次心跳以上应显示陈旧；断线和重启后补传保留原始发生时间并幂等落库。
- 采集身份不可写其他 VM，匿名注册和心跳应拒绝；浏览器 Cookie 写入必须通过 CSRF。
- Docker 采集代理只允许必要 GET；create、exec、update、kill 与 delete 拒绝。
- 缺失 CA、主机名不匹配和过期证书连接拒绝。平台只读权限不足和设备缺失如实显示。
- 未安装 GPU 时仅能验证 CUDA OOM 回放，不能声明真实 GPU 映射或归因。

## 8. 自动化与交付

安装开发和浏览器依赖后分开运行后端与浏览器测试，避免同步 Playwright 和 async 测试共用事件循环：

```bash
python -m pytest tests/contract tests/unit tests/integration tests/scenario tests/security -q
python -m pytest tests/e2e -q
```

测试使用隔离数据库；不得将测试数据、测试恢复或保存截图称为真实业务证据。性能、容量和探针开销仅使用实际报告中的数值。

UI 详细点按见 `docs/ui-operation-guide.md`；录屏镜头和口播见 `docs/video-narration.md`；交付归档见 `docs/assets-delivery-index.md`。打包脚本对凭据、路径安全、核心资产和文件摘要进行验证，模型与可选离线镜像单独归档。

停止控制面使用 `docker compose -f deploy/compose.yaml down`，默认保留数据卷。正式卸载应先成功撤销实际探针凭证，再移除注册配置；删除数据是独立且明确指定目标的操作。

