# 实际环境与能力边界

核验日期：2026-10-15，Asia/Shanghai。环境是 Windows / WSL2 中运行的嵌套 KVM 实验平台；业务实际运行在 ZSvirt 管理的 Linux VM 内。

| 项目 | 实际配置或结论 |
|---|---|
| 平台 | GetVersion 实际返回 ZSvirt 1.0.0；源 qcow2 为 1.0.0-h84r；真实 host 为 Enabled / Connected |
| 平台磁盘 | 官方 h84r qcow2，SHA256 383099b3ad9cfd793dfd49e4b2dc99743832073d3c39e86deae91be59e5d9c3b；下载原件保留，运行使用 backing overlay |
| 外层虚拟机 | QEMU / KVM，8 vCPU、8 GiB；WSL 同时承载控制面、数据库及 Prometheus |
| 业务 VM | UUID 131f36d6497247ce9f99000949c82fd3；Ubuntu 24.04.5；2 vCPU、3 GiB、20 GiB；IP 10.50.0.162 |
| 实际 VM CPU 配额 | libvirt period=1000000、quota=1000000 作用于每个 vCPU；两个 vCPU 各有一核预算，父 cgroup 无总配额限制；KVM 与两个 vCPU 的文件描述符、cgroup 实测见 `virtualization-performance.json` |
| 推理 CPU 特性 | 默认 qemu64 不满足该推理镜像所需特性；实验平台通过 API 启用 host-passthrough，业务 VM 可见 AVX2 |
| 模型 | Qwen2.5-0.5B-Instruct Q4_K_M，491400032 bytes；llama.cpp build 11610，CPU、单并发、单线程、1024 context |
| VM 容器运行时 | Docker 29.1.3 / Compose 2.40.3 |
| 当前控制面 | Python 3.12、原生 PostgreSQL 16.15；交付镜像单独使用固定 PostgreSQL 16.4 验证 |
| 指标 | VM 探针9108，经 SSH 端口转发到中心19108；Prometheus v3.5.0，实验抓取周期5秒 |
| 事件 | Docker 原始发生时间、库存、任务分段事件进入真实 API 与正式 worker |
| GPU/vGPU | 当前无硬件，映射能力明确为 false；CUDA/GPU 仅提供带回放来源的验证入口 |

## 平台接口与权限

四项查询：`/hosts`、`/vm-instances`、`/zwatch/alarm-records`、`/zwatch/event-records`。认证使用 OAuth 会话，支持账户登录和失效后的有界续期。当前只读 Normal 账户及精确查询策略已实际验证，VM 同名修改被 SYS.1019 拒绝。原生记录可见性角色的机制见 `zsvirt-readonly.md`。

平台已提供真实 host、VM 及原生告警/事件。当前适配范围为 host/VM，未发现的存储和管理节点引用保持 unresolved；不构造历史资源。平台利用率、VM CPU quota 和 GPU 进程映射没有通过平台适配器进入诊断。quota 的环境核验来自只读 libvirt 检查，不冒充平台指标能力。

## 网络与安全

中心与 VM 的 HTTP 只监听各自 loopback，并通过已认证 SSH 隧道连接；这次实验没有部署公网 TLS 入口。跨主机常规部署提供 Caddy / CA 配置，必须使用可信 HTTPS。未配置的 exporter 和 tracing 不显示为已部署能力。

平台管理员和 SSH root 的初始口令已改为随机值。业务用户不加入 docker 组，采集走受限只读代理，实验 CLI 使用独立 sudo 权限。秘密保留在本机权限受限的 env 文件，交付包排除这些文件。

## 环境限制

host-passthrough 依赖宿主 CPU，跨不同 CPU 主机迁移须先验证兼容性。嵌套虚拟化、宿主交换及共用物理资源会影响推理延迟，测量结果只适用于本次环境。平台管理节点使用官方机制设置 Java 最大堆 3 GiB，实际进程 `VmSwap=0`；WSL/QEMU 的交换和 VM 内 CPU 竞争仍存在。正常基线中的慢请求原样保留。完整 ZSvirt、控制面与业务 VM 需要分别预算；业务 VM 的 2 vCPU / 3 GiB 不代表整套平台预算。


## 实测运行身份与维护条件

业务 VM boot_id 为 `2d8868ce-72f5-40d9-bbd5-0de432115b8d`，本次观测起点为 `2026-10-15T06:40:07+00:00`。系统自动更新造成过 CPU 竞争及 Docker 统计超时，原始失败与恢复记录保留。正式验证在既有包事务完成、dpkg 状态正常、容器身份和配额复核后运行；验收期间仅暂停本次启动内的 apt 定时调度，系统更新服务未被强制终止。重新启动会恢复已启用的定时器。

正式采集日志窗口从保存的健康预检时刻 2026-10-15T07:49:34.669457+00:00 起计，覆盖随后正式实验和探针测量。窗口内捕获的网络异常单独计数；系统维护期间的代理请求异常与全启动期原始失败结论另行保留。日志核查不等同于整个启动期没有异常，范围依据见 evidence/final/formal-observer-window-basis.json。
