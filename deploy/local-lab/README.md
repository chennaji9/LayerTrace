# 本地 ZSvirt 实验环境启动与恢复

本机入口为 `D:\ZSvirt\layertrace-runtime\lab-runtime.py`，配置为同目录 `runtime.json`。在 WSL 中执行；Python 仅使用标准库，系统需有 QEMU/KVM、OpenSSH、Docker CLI 和 PostgreSQL 16 管理工具。实际控制面源码路径由配置 `project_root` 指定，`.venv` 必须可用。配置保存路径、端口和资源 UUID，凭据从受保护的 `control-plane.env`、`local-secrets.env`、`ssh_key` 读取；这些文件不进入源码交付包。

```powershell
wsl.exe -- sudo python3 /mnt/d/ZSvirt/layertrace-runtime/lab-runtime.py status
wsl.exe -- sudo python3 /mnt/d/ZSvirt/layertrace-runtime/lab-runtime.py up
```

`status` 读取进程、网络和 HTTP 健康状态。`up` 按 QEMU → 外层 SSH → 业务接口/网桥/NAT → ZSvirt API 就绪 → 已有业务 VM → PostgreSQL → 控制面/worker → Prometheus → SSH 隧道的顺序检查并启动缺失组件。健康进程保留原 PID；过期 PID 通过完整命令签名核对，拒绝接管多个匹配进程。已有数据库、模型、容器、结果和探针队列保留。平台冷启动时，入口容忍 API 连接重置和尚未就绪的响应，并等待 VM 的 Unknown/Starting/Stopping/Rebooting 状态结束；等待上限由 `boot_timeout_seconds` 指定。业务 VM 仅在官方 API 显示 Stopped 时执行 StartVmInstance，超时或其他状态会停止恢复，不创建替代 VM。

管理账号会话过期时，从 `local-secrets.env` 的 `ZSVIRT_ADMIN_PASSWORD` 生成 SHA-512 并调用正式登录接口。只读采集身份由控制面的平台适配器管理。SSH 使用本地记录的主机公钥，遇到公钥变化会停止；迁移或重新建立 VM 后须由操作员核验新主机公钥。

需要显式恢复业务 Compose 时执行：

```powershell
wsl.exe -- sudo python3 /mnt/d/ZSvirt/layertrace-runtime/lab-runtime.py up --resume-workload
```

此选项在业务 VM 的 `/opt/layertrace` 使用 `workload.env` 和六个 Compose 文件：探针基础配置、AI 工作负载、host network、离线镜像、独立客户端覆盖文件和探针固定镜像覆盖文件。若 `evidence` 中有仍在 prepared/injecting/running/restoring 的真实实验状态，脚本停止业务恢复，先核对并运行该实验的 `restore`。默认 `up` 只恢复基础设施和隧道，业务服务依照其已保存的 Docker restart policy 恢复；手工停止的客户端保留停止状态。

外层 ZSvirt 的专属网络服务为 `layertrace-lab-network.service`，启动脚本 `/usr/local/sbin/layertrace-lab-network`，sysctl 配置 `/etc/sysctl.d/99-layertrace-lab.conf`。服务建立 ZSvirt L2 网络登记的 `lab0` dummy 接口，将其接入 `br_lab0`，配置 `10.50.0.1/24` 和 `10.50.0.0/24 → br_dvs0` 的 NAT。已有 `lab0` 必须仍是 dummy 类型，且未归属其他网桥；类型或归属不符时停止。脚本仅添加缺失的地址与规则，不刷新 iptables。源码中 `network.sh check` 可单独检查；`network-install` 负责安装并启用该服务。

```powershell
wsl.exe -- sudo python3 /mnt/d/ZSvirt/layertrace-runtime/lab-runtime.py network-install
wsl.exe -- sudo python3 /mnt/d/ZSvirt/layertrace-runtime/lab-runtime.py tunnel-up
```

隧道把业务 VM 回环 18000 转发到控制面 8000，把本机回环 19108/18091 转发到探针 9108/Agent 8091。`tunnel-up` 保留已匹配进程；`tunnel-down` 仅对经过签名核对的隧道 PID 发 SIGTERM。录制与真实故障期间须持续保持隧道。控制面断开时 probe outbox 保留待发送数据，来源状态进入 stale 后再恢复；短断联的结果以交付证据为准。

当前外层 QEMU 使用 8 vCPU、8192 MiB、`q35,accel=kvm` 与 `-cpu host`。业务 VM 为 2 vCPU、3 GiB，采用 host-passthrough；每个 vCPU 的 quota/period 均为 1000000/1000000，合计为两核计算预算。模型默认 `MODEL_THREADS=1`、context 1024、单并发、CPU 推理，容器配额上限为 2 核。线程和超时配置改变后须重新积累正常任务基线，不沿用另一负载配置的 P95。

本机管理节点通过 ZSvirt 官方 `setenv` 配置 `CATALINA_OPTS=-Xmx3072M`，Java heap 上限为 3 GiB；该配置适用于外层 8 GiB 的本地实验环境。恢复入口保留平台中已保存的管理节点环境配置，不执行 `setenv` 或管理节点重启。管理节点冷启动和业务 VM 同时运行时，应检查宿主剩余内存与 swap 后再开始性能录制。

原始镜像为 `D:\ZSvirt\ZSvirt-x86_64-1.0.0-h84r.qcow2`；运行镜像为 `D:\ZSvirt\layertrace-runtime\zsvirt-lab.qcow2`。后者是带绝对 backing path 的写入层，包含平台配置与业务 VM 磁盘，不能单独复制到别处运行。恢复入口要求原镜像与写入层均存在，且 backing path 一致，不生成或覆盖镜像。迁移前应先关闭业务 VM 与外层平台，保留两层磁盘及原路径，或由操作员在离线状态转换为完整磁盘并重新配置；不要对运行中的写入层执行 rebase/convert。源码包中的恢复脚本是现有本机环境的运维入口，重建另一套 ZSvirt 环境需按架构说明重新导入基础镜像并填写新的真实资源 UUID。

Ubuntu 基础镜像为 `ubuntu-24.04-minimal-cloudimg-amd64.img`，264503296 字节。SHA-256 为 `2f119f50bf29fe40e73b69ae2a736e8210d8146796f036e0324315b6b3a777df`，与 Ubuntu 官方 `minimal/releases/noble/release/SHA256SUMS` 一致；校验文件及下载源记录保存在资产目录，详见 `assets-manifest.json`。模型、探针和工作负载镜像使用同一资产清单校验。

网络与隧道的幂等检查记录位于 `layertrace-runtime/evidence/local-recovery-check.json`。本机还完成了 WSL 重启后的现有平台、业务 VM、控制面与采集通道恢复；新启动身份、镜像和源码核对记录位于 `evidence/final-20261015-r5/post-restart-native-source-verification.json`，四来源健康及零队列记录位于同目录 `pre-baseline-readiness.json`。API 启动等待、VM 过渡状态、超时和资源缺失分支通过 6 项针对性测试。以上范围是已有持久化环境恢复，全新主机的完整重建仍需按部署文档另行验证。

## 探针开销采样

在业务 VM 的 `/opt/layertrace` 使用 `sudo bash deploy/measure-probe-overhead.sh sample --mode on --seconds 900 --interval 5 --output evidence/overhead`，再用相同参数的 `--mode off`。此入口只观察已运行且未暂停的一个 demo-client，要求默认持续模式或 `--count 0`，不启动新客户端，不改模型、业务容器或结果文件。停用档暂停原 ID 的 probe 与只读 Docker gateway，保留 SQLite outbox；采样完成、收到中断信号或独立 watchdog 到期后恢复两组件并移除专属 iptables 计数链。成功报告仅在恢复验证通过后保存，报告与状态文件均须满足 `restored=true` 后才可开始下一次采样；异常情况下执行 `sudo bash deploy/measure-probe-overhead.sh restore --state evidence/overhead/<run_id>/state.json`。

采样按 cgroup CPU 累计值差分计算平均单核 CPU%，汇总该 cgroup 所有进程的 RSS 和磁盘 I/O，记录状态卷峰值及 probe 专属出站 IP 字节，并只读保存窗口前、窗口后与恢复后立即的 outbox 深度、累计丢弃数和 gap。独立采样器的 CPU 不计入 probe 组件。默认 600 秒的队列保留时间短于 900 秒暂停窗口，被暂停探针也无法采集新遥测，因此这个 A/B 不证明暂停期间零丢失。业务侧事件 emitter 在两档持续启用，成功任务耗时是 Agent 处理时间；该结果只适用于当前 Qwen CPU、单并发和既定发送间隔。详细口径、环境指纹和实测数据见 `docs/probe-overhead.md`。

## 断联、探针重启与补传验证

在真实故障和 A/B 均已恢复、四个采集来源健康时，使用 `reconnect_check.py preflight` 做只读检查；`run --seconds 60` 才会执行独立实验。脚本要求原采集隧道运行、SQLite outbox 为空、探针凭据剩余有效期足够，拒绝未恢复的真实故障及采样。

```powershell
wsl.exe -- sudo python3 '/mnt/f/研究生/2026-9-开源大赛/层迹 · LayerTrace/deploy/local-lab/reconnect_check.py' preflight
wsl.exe -- sudo python3 '/mnt/f/研究生/2026-9-开源大赛/层迹 · LayerTrace/deploy/local-lab/reconnect_check.py' run --seconds 60
```

断联只停止已通过命令签名核对的 SSH 采集隧道。脚本通过独立管理 SSH 保存 SQLite 已提交事件的 ID、原发生时间和摘要，再重启保存的原 probe 容器，核对同一容器的新启动时间及队列内容保留。模型、Agent、工具与 Docker 网关的身份和配置保持一致。控制面达到 stale 后记录公开状态和工作负载接口；独立恢复进程按 monotonic 截止时间重建隧道，报告包含实际断联秒数。

恢复后等待队列排空与四来源健康，使用只读数据库事务核对原事件时间和唯一落库，再通过真实 probe 上报接口重复提交同一批已确认事件，要求 HTTP 202、accepted=0、duplicate=原批条数。该重放只验证幂等，不生成新的观测事件。凭据不写入报告，完整事件正文只保存在 VM 的专属临时验证目录。

证据保存到 `D:\ZSvirt\layertrace-runtime\evidence\probe-reconnect\<run_id>`。WSL 侧独立 watchdog 与 VM 侧限时恢复进程在断联前启动；VM 侧持有与开销采样相同的锁直到验证结束。异常手工恢复使用 `reconnect_check.py restore --state <绝对路径>/state.json`，只处理保存的原 probe ID 和采集隧道。平台或 VM 断电不在这次短断联实验的覆盖范围内。

独立客户端的镜像、源码摘要、专用导入工具和客户端覆盖文件及第六个探针固定镜像覆盖文件见 [独立客户端镜像说明](../../docs/client-image.md)。
