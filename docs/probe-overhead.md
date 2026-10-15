# 探针开销与断线补传

on/off 各 900.161/900.098 秒，5 秒采样；探针加只读代理 CPU 均值 9.161%/0.000%（单核口径），同步 RSS 均值 60.704/60.703 MiB。

同一持续客户端以 2 秒间隔串行发起业务，on/off 两段顺序执行。off 暂停探针与只读 Docker 代理，RSS 仍驻留；只测这两个组件，不包含 dockerd、containerd、业务事件发送器、控制面、Prometheus 或独立采样器。

| 模式 | 时长（秒） | 样本数 | CPU 单核均值（%） | 同步 RSS 均值 / P95 / 峰值（MiB） | 成功业务数 | 任务处理 P95（ms） |
|---|---:|---:|---:|---|---:|---:|
| on | 900.161 | 176 | 9.161 | 60.704 / 60.707 / 60.707 | 36 | 32845 |
| off | 900.098 | 178 | 0.000 | 60.703 / 60.703 / 60.703 | 48 | 18205 |

任务处理 P95 的顺序差异为 80.417%；共享宿主、顺序窗口和有限样本不支持将差异全部归因于探针。每项分段耗时保留自己的样本数；跨边界任务在原报告中排除。

60 秒断隧道并重启原探针的验证保留安装身份、持久队列、原 event_id / occurred_at 与属性摘要，重复重放返回 HTTP 202，最终队列为 0、新增 dropped 为 0。历史 dropped/gap 保留；15 分钟暂停超过 600 秒留存设置，不能据最终空队列宣称全程零丢失。

探针加代理的 I/O、上报网络字节、队列和业务完整样本见材料 `evidence/final/overhead/measurement-observed-summary.json` 及其逐项 SHA256 绑定原件。断线原件路径见 `evidence/final/reconnect-selection.json`。

## 采样方法与口径

在业务 VM 的 `/opt/layertrace` 执行，保留同一已运行的 demo-client、模型、Agent 和工具容器及发送间隔。客户端须使用默认持续模式或 `--count 0`，有限条数的客户端不能用于这两个窗口：

```bash
sudo bash deploy/measure-probe-overhead.sh sample --mode on --seconds 900 --interval 5 --output evidence/overhead
sudo bash deploy/measure-probe-overhead.sh sample --mode off --seconds 900 --interval 5 --output evidence/overhead
```

每档保存 `samples.jsonl`、`state.json` 与独立恢复进程日志；仅在恢复验证通过后保存成功的 `report.json`。启用档观察两个探针组件；停用档对其原完整容器 ID 执行 pause，再自动 unpause，保留全部业务容器与 outbox。成功恢复时状态文件和报告均记录 `restored=true`。主进程中断或 SIGKILL 时由独立 watchdog 到期恢复；VM 断电后需要根据状态文件手工恢复。采样入口拒绝并发执行、仍未恢复的真实故障和已暂停的客户端。

| 数据 | 定义 |
| --- | --- |
| 平均 CPU | cgroup v2 的 `usage_usec` 差值除以实际窗口秒数，单核为 100%；两组件求和 |
| RSS | 每次采样汇总目标 cgroup 内全部进程 `VmRSS`；报告均值和峰值 |
| cgroup memory | `memory.current`，包括内核收费及缓存，与 RSS 分列 |
| 状态卷峰值 | `/var/lib/layertrace` 的逻辑字节和实际分配字节峰值 |
| 磁盘写入 | cgroup `io.stat` 的写字节差值；进程 `/proc/pid/io` 同时记录供核对 |
| API 出站量 | guest OUTPUT 中仅匹配 probe cgroup、回环 TCP 目标 18000 的 IP 字节 |
| 指标响应量 | 同一匹配范围中、回环 TCP 源端口 9108 的 IP 字节 |
| 任务耗时 | 成功任务的 Agent 处理时间，包括模型/工具调用和事件入队；排除跨窗口任务 |
| outbox 状态 | 资源采样窗口前、窗口后及恢复后立即进行只读汇总，记录深度、最旧事件时间、累计丢弃数及 gap，不复制数据库或事件正文 |

网络计数包含 IP/TCP 头、ACK 和重传，不是 JSON 正文大小，也不含加密 SSH 包络或其他业务 cgroup 流量。计数使用专属临时 iptables 链，RETURN 到原有 OUTPUT 流程，不改变防火墙接受或丢弃策略；完成和异常恢复时移除该链。独立采样器在两档使用同一实现，其 CPU 不进入探针组件计数。

业务侧事件 emitter 在两档持续工作，因此该 A/B 比较的是启用、暂停 VM 探针及 Docker 只读网关时的业务表现。任务耗时可能受到 Docker 守护进程等间接变化影响，但不能据此分离其资源成本。RSS 在 pause 档仍保留，不能把它解释成卸载后的内存；停用档期间 probe 心跳与指标暂停，但已有任务事件仍由业务侧发送。顺序窗口、有限样本以及外层宿主资源波动限制了延迟因果结论。结果仅适用于声明的 CPU 模型、配额、模型参数与负载，不外推至 GPU、大模型或 OTel Collector 配置。

默认 outbox 保留时间为 600 秒，900 秒暂停可能使此前未确认事件在恢复后按保留策略过期，且被暂停的探针无法采集新的 VM 遥测。恢复后的队列汇总为立即快照，不能单独证明后续全部排空；丢弃数与 gap 均以原始记录为准。短断联的持久化、重启补传和幂等结论由独立的 60 秒断联实验验证。

异常手工恢复：

```bash
sudo bash deploy/measure-probe-overhead.sh restore --state evidence/overhead/<run_id>/state.json
```

恢复只 unpause 由采样器记录的原 ID，保留重建后的同名组件。若容器已被替换或恢复仍失败，应先核对原 ID 与专属计数链，不能直接开始下一档采样。
