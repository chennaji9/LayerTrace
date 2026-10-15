# VM 探针与 AI 工作负载部署

在 ZSvirt 管理的业务 Linux VM 内运行。推荐配置 4 vCPU、4 GiB 内存，Docker Engine 和 Compose v2；模型占约 469 MiB 磁盘，服务内存预算约 2.2 GiB。本次实际业务 VM 为 2 vCPU、3 GiB，运行与场景结果见交付证据；它不代表生产最低容量。CPU 模型、Agent、工具、客户端各为独立容器，任务结果来自本地真实推理。CPU 模型镜像需要 guest CPU 暴露匹配的指令集，本次配置使用 host-passthrough；仅分配 vCPU 数量不能代替 CPU 类型核对。

本地单节点 ZSvirt 的业务 VM 为 2 vCPU，每个 vCPU 的 quota/period 均为 1000000/1000000，两个 vCPU 合计为两核计算预算。模型线程由 `MODEL_THREADS` 设置，默认 1；容器自身仍保留 2 核上限，context 为 1024，模型并发为 1，使用 CPU 推理。Linux `lscpu` 应可见匹配的 AVX/AVX2 指令。本机 `layertrace` 业务用户没有 Docker 组权限，下列管理命令使用 `sudo`；这不改变探针容器的非 root 身份或只读 Docker 代理权限。

先从完整源码目录获取模型：`bash deploy/workload/fetch-model.sh /opt/layertrace-assets`。复制 `deploy/vm-probe/env.example` 为 `deploy/vm-probe/.env`，填写真实平台 VM UUID、持久安装 UUID、控制面 URL、注册 secret 和项目写入凭据。HTTPS 使用受信 CA；私有 CA 放在 certs 目录。若同 VM 私网部署使用 HTTP，控制面应按其安全配置显式允许该精确地址范围。

执行 `sudo bash deploy/vm-probe/install.sh`。此脚本将两个 Compose 文件合并为一个 `layertrace-vm` 项目，避免分别运行时网络和身份卷分离。只部署探针时可以单独使用 `deploy/vm-probe/compose.yaml`；独立工作负载必须与同一身份卷及网络连接。本机离线 SSH 回环部署使用后文的六个 Compose 文件及 `workload.env`。

探针通过只允许 GET 的 Docker socket 代理访问列表、inspect、stats 和有界 events 接口，拒绝 create/exec/kill/update/delete。代理不发布宿主机端口，只有探针挂接该内网。探针自身以 UID 10001 运行，凭据与持久 outbox 仅在专属卷中；工作负载只读挂载不含凭据的身份卷。工作负载需要 `layertrace.observe=true` 标签才会被发现。故障操作还必须有 `layertrace.lab=true` 与匹配的服务标签。

Agent HTTP 入口绑定 VM loopback 8091；模型 8080 和工具 8090 仅在容器网内。指标服务 9108 默认绑定 VM loopback。远程 Prometheus 采集时将 `PROBE_METRICS_BIND` 设置为 guest 内网地址，并将防火墙入站限制到控制面地址。指标标签使用完整 container_id、instance_id、VM 与 probe ID，不包含 task_id。样本过期时 exporter 停止提供旧样本且 `probe_up=0`。

正常业务可请求：

```sh
curl -s http://127.0.0.1:8091/tasks -H 'Content-Type: application/json' -d '{"task_id":"manual-unique-001","topic":"cpu"}'
```

每次任务 ID 必须唯一。返回 `status`、真实 `model_answer`、工具 `tool_result`、模型结合两者生成的 `summary` 与耗时；这些正文保存在客户端结果，遥测不包含提示词和回答。

客户端默认循环运行，结果在 client-results 卷的 `tasks.jsonl`。故障前至少积累 30 个成功任务。额外客户端可用完整合并命令运行 `run --rm demo-client python -m workloads.demo_agent.load_generator --count 35 --interval 1`；所有 Compose 命令必须加与安装相同的 project-name、env-file 和全部覆盖文件。串行客户端与额外客户端应依次运行，避免改变模型并发与基线负载。

真实实验在业务 VM 的源码目录执行：

```sh
sudo python3 -m scenarios.real_lab run process-failure --target layertrace-demo-agent --seconds 45 --output evidence/lab
sudo python3 -m scenarios.real_lab run cpu-quota --target FULL_MODEL_CONTAINER_ID --cpus 0.1 --seconds 180 --output evidence/lab
sudo python3 -m scenarios.real_lab run tool-503 --target layertrace-demo-tool --seconds 45 --output evidence/lab
```

进程实验明确是 SIGKILL 引起的中断，不能将 exit code 137 单独称为 OOM。每次实验保存原限制、原重启策略和完整容器 ID，在注入前启动独立限时恢复进程，并在恢复后重新 inspect 验证。工具实验从容器内部请求真实 HTTP，记录 503，故障文件本身也有 TTL。CLI 被强杀或终端断开后，独立进程仍会到期恢复；主进程、独立进程与手工恢复通过文件锁串行操作同一状态。VM 断电会同时停止这些进程，重启后须执行 `sudo python3 -m scenarios.real_lab restore --state evidence/lab/<run_id>/state.json`。恢复只操作保存的完整 ID，拒绝对同名替代容器操作；状态、watchdog 日志及验证结果保存在实验目录。

探针凭据有效期由控制面设置，默认 600 秒，探针在到期前 60 秒续注册；注册 secret 本身不是自动过期令牌。待发送事件在确认整批落库后才删除，网络断线或重启会保留事件；队列超出容量或时间窗时会记录丢弃数及 gap。

卸载在源码根目录执行 `bash deploy/uninstall.sh --target vm --env-file deploy/vm-probe/.env`，并追加与实际安装一致的 `--compose-overlay` 文件。脚本暂停探针，调用管理员 `POST /api/v1/probes/{probe_id}:revoke`，确认 204 且旧 heartbeat 被拒绝后，再停止合并 Compose 并仅删除状态卷中的 `probe-credential.json`，清空采集侧 secret 与项目 token。撤销失败时恢复探针，服务与本地凭据保留。默认保留结果与 SQLite 队列卷，删除数据须明确选定卷。

服务器撤销会保留 tombstone，阻止旧安装 ID 自动续注册。重新部署须使用新授权安装 ID 并核对真实 VM UUID。控制面卸载入口为 `bash deploy/uninstall.sh --target control-plane --env-file .env`；它先撤销当前项目探针再停止控制面。私有 CA 提供脚本主机上的真实路径 `--ca-file`，无法从容器读取凭据时用受保护的 `--credential-file`，完整权限说明见 `docs/permissions.md`。

## SSH 回环部署与离线镜像

控制面在另一台主机、业务 VM 使用 SSH 时，可由控制面主机保持以下隧道（替换 VM 地址）：

```sh
ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -N \
  -R 127.0.0.1:18000:127.0.0.1:8000 \
  -L 127.0.0.1:19108:127.0.0.1:9108 \
  layertrace@VM_IP
```

业务 VM 的 18000 仅监听 loopback，HTTP 流量经 SSH 加密传至控制面 8000。控制面本地 19108 转发到 VM 探针 9108，供 Prometheus 或本地检查使用。此隧道需要在整个实验与录制期间保持运行；失联时探针缓存待发送事件，来源状态应显示陈旧。若 Prometheus 自身在容器中，需将抓取地址与容器网络连通方式明确配置，不能使用容器自己的 127.0.0.1。

离线传入模型目录和镜像归档，校验 assets-manifest.json 中的 SHA-256，然后在业务 VM 执行：

```sh
sudo python3 scripts/load_offline_images.py \
  --archive /opt/layertrace-assets/layertrace-vm-images.tar \
  --manifest /opt/layertrace-assets/probe-images-manifest.json
sudo python3 scripts/load_offline_images.py \
  --archive /opt/layertrace-assets/layertrace-workload-image.tar \
  --manifest /opt/layertrace-assets/workload-images-manifest.json
sudo python3 scripts/load_client_image.py \
  --archive /opt/layertrace-assets/layertrace-client-image.tar \
  --manifest /opt/layertrace-assets/client-images-manifest.json
mkdir -p deploy/vm-probe/certs
cp deploy/vm-probe/env.tunnel.example workload.env
chmod 600 workload.env
# 填写真实 VM UUID、安装 ID、注册 secret、项目写入 token，以及实际模型目录。
sudo docker compose --project-name layertrace-vm --env-file workload.env \
  -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
  -f deploy/vm-probe/compose.host-network.yaml -f deploy/workload/compose.offline.yaml \
  -f deploy/workload/compose.client-fixed.yaml -f deploy/vm-probe/compose.final-images.yaml \
  up -d --no-build --pull never
```

此组合采用 host network，proxy、探针、模型、工具与 Agent 的监听均限定 VM loopback；只有明确的 SSH 转发能跨主机访问。Compose v2.24 及以上支持覆盖文件中的 !reset。镜像从受校验归档导入，CPU 模型使用 layertrace-cpu-model:locked，其内容对应模型清单中的固定镜像摘要。

以下命令为实验使用相同 Compose 参数的便捷函数：

```sh
ltcompose() {
  sudo docker compose --project-name layertrace-vm --env-file workload.env \
    -f deploy/vm-probe/compose.yaml -f deploy/workload/compose.yaml \
    -f deploy/vm-probe/compose.host-network.yaml -f deploy/workload/compose.offline.yaml \
    -f deploy/workload/compose.client-fixed.yaml -f deploy/vm-probe/compose.final-images.yaml "$@"
}
ltcompose ps
ltcompose logs --tail 30 vm-probe demo-agent demo-model
ltcompose stop demo-client
ltcompose run --rm --no-deps demo-client python -m workloads.demo_agent.load_generator --count 35 --interval 0.1
ltcompose up -d --no-build --pull never demo-client
```

启动等待会先确认探针在当前进程启动后重新观测了 Agent 身份，再检查模型与工具就绪；独立客户端首次等待 Agent 健康后才开始产生任务。此顺序避免把服务初始化当作故障，重启代次也不会沿用身份文件中的旧实例。

本机已部署源码目录为 `/opt/layertrace`，模型目录为 `/opt/layertrace-models`，管理配置为 `/opt/layertrace/workload.env`；在该目录调用 `ltcompose`。WSL 中控制面端口 8000、Prometheus 19090、探针转发 19108 和 Agent 转发 18091 均监听回环地址。本机启动与恢复入口见 `deploy/local-lab/README.md`。

故障按进程 45 秒、CPU 配额 180 秒、工具 503 45 秒执行；每个场景结束后至少等待一个 15 秒心跳窗口，并确认新任务成功。恢复报告须同时检查 CLI 的 restore_verified、业务成功与控制面事件单恢复状态。
