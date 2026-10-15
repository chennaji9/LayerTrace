# 复现报告

记录日期：2026-10-15，Asia/Shanghai。控制面自动化在隔离测试库中执行；AI 业务、探针和真实故障运行在 ZSvirt 管理的 Linux VM 中。实验环境和第三方完整复现分别记录。

## 环境与资产

| 项目 | 保存证据中的实际配置 |
|---|---|
| 宿主与控制面 | Windows / WSL2；Python 3.12；原生 PostgreSQL 16.15 |
| 平台 | ZSvirt GetVersion 返回 1.0.0；源 qcow2 标识 1.0.0-h84r；QEMU / KVM |
| 业务 VM | Ubuntu 24.04.5；内核 7.0.0-38-generic；2 vCPU、3 GiB、20 GiB；平台 UUID `131f36d6497247ce9f99000949c82fd3` |
| VM 容器运行时 | Docker 29.1.3、Compose 2.40.3、cgroup v2 |
| CPU 模型 | Qwen2.5-0.5B-Instruct Q4_K_M；llama.cpp build 11610；单线程、单并发、1024 context；无 GPU |
| 控制面离线身份 | `sha256:18803bb39692e0b1d9d739233dcd84485a71c59b38814f809786679ab22bd616`；`layertrace-control-plane:delivery-20261015-r4` |
| 隔离空库检查 | PostgreSQL 16.4 固定镜像；Alembic 迁移与正式 API / worker |
| 采集链路 | VM 内只读 Docker 代理与探针；Prometheus 5 秒抓取；经认证 SSH 回环隧道连接控制面 |

每个 vCPU 的 libvirt quota/period 为 1000000/1000000，两者合计为两核计算预算。模型单线程不改变 VM 的总 CPU 预算。本机控制面为 WSL 虚拟环境下的 web / worker，交付镜像另行完成导入与空库检查。控制面、平台与业务 VM 共用宿主资源，独立正常批次不能据此得出独占宿主性能结论。最终启动代次与采集状态以材料 `evidence/final/environment-observed-final-delivery.json` 为准。

模型大小为 491400032 bytes，SHA256 为 `74a4da8c9fdbcd15bd1f6d01d621410d31c6fc00986f5eb687824e7b93d7a9db`。模型权重与原始 Apache-2.0 许可、推理镜像与 MIT 许可分别提供；平台磁盘属于外部环境资产。版本、来源和许可证见 [资产索引](assets-delivery-index.md)。

## 独立检查与证据入口

| 检查 | 保存结果 | 证据入口 |
|---|---|---|
| 契约、单元、集成、场景、安全与性能 | 364 passed，157.69 秒 | [Core 原始日志](evidence/control-plane-acceptance/r4/tests-core-r4.log.txt) |
| 浏览器端到端 | 20 passed，262.67 秒；普通登录、签名会话、页面和历史诊断 | [E2E 原始日志](evidence/control-plane-acceptance/r4/tests-e2e-r4.log.txt) |
| Ruff | All checks passed | [静态检查日志](evidence/control-plane-acceptance/r4/ruff-r4.log.txt) |
| 固定镜像与运行源码 | 152 个指定文件 SHA256 一致 | [源码一致性记录](evidence/control-plane-acceptance/r4/image-source-parity.json) |
| PostgreSQL 16.4 空库 | 49 项检查通过；60 秒稳定恢复门槛实等 60.79 秒；临时资源清理通过 | [空库报告](evidence/control-plane-acceptance/r4/cold-smoke/report.json) |
| 平台只读接入 | host、VM、原生告警与事件查询；Normal 身份写请求被拒绝 | [只读权限机制](zsvirt-readonly.md)；材料 `evidence/readonly-platform-native.json` |
| 独立正常业务 | 35/35 成功；任务内累计模型 P95 25,843 ms，任务处理总耗时 P95 26,393 ms，工具 P95 853 ms。R2 另使用所选诊断保存时刻的健康滚动基线。 | 材料 `evidence/final/baseline-business.jsonl` 及对应 summary / client、独立审阅；任务处理耗时为 Agent 处理总耗时，客户端往返时间另列 |
| 真实独立 OOM 反例 | 无依赖容器 OOM 未归为正常业务的 high OOM 根因 | 材料 `evidence/negative-cases/independent-oom-final.json` |

测试使用独立数据库，不能在现有演示数据库执行会清表的夹具。浏览器历史专项与完整 E2E 有重合，单独保留、不累计为额外完整验收。全部测试范围与限制见 [测试报告](test-report.md)。

## 真实实验与可靠性

<!-- real-validation:begin -->
| 检验 | 结果 | 实际证据与范围 |
|---|---|---|
| 独立正常业务 | 35/35 成功 | 35/35 成功；任务内累计模型 P95 25,843 ms，任务处理总耗时 P95 26,393 ms，工具 P95 853 ms。R2 另使用所选诊断保存时刻的健康滚动基线。 材料 `evidence/final/baseline-summary.json` 与原始 JSONL |
| CPU 配额 | 5 次独立通过 | run `lab-3662671a424e`；task `task-efbafb89aac0432c9a758ac71cf7f0d9`；incident `a72c7605-085f-4148-81b6-cd6810257811`；R2/high，第 239 轮；故障至首个合格诊断 min/median/max=111.193/118.368/130.091 秒。原件 `evidence/runs/cpu-quota-24-20261015T081443-af2d0c/manifest.json` |
| 容器进程退出 | 5 次独立通过 | run `lab-d968a3a2199e`；task `task-ae216e58d10447b8997cdcc9ffc1060b`；incident `ae899a52-7f37-420d-83ae-073623ae4ed8`；R0/high，第 38 轮；故障至首个合格诊断 min/median/max=14.274/19.915/20.996 秒。原件 `evidence/runs/process-failure-20-20261015T085519-f2e263/manifest.json` |
| 工具 HTTP 503 | 5 次独立通过 | run `lab-7706505db9c4`；task `task-b5cb10c81f7048ed808b1a14eb307e35`；incident `b6506dbf-0611-4358-98bf-13ed78d44ed8`；R3/high，第 10 轮；故障至首个合格诊断 min/median/max=53.116/67.027/79.297 秒。原件 `evidence/runs/tool-503-15-20261015T092657-cf82fa/manifest.json` |
| 探针顺序 A/B | 完成并恢复 | on/off 各 900.161/900.098 秒，5 秒采样；探针加只读代理 CPU 均值 9.161%/0.000%（单核口径），同步 RSS 均值 60.704/60.703 MiB。 材料 `evidence/final/overhead/measurement-observed-summary.json` |
| 60 秒断联、原探针重启与补传 | 完成并恢复 | 保留原 event_id / occurred_at，重复重放返回 HTTP 202，最终队列 0，新增 dropped 为 0；材料 `evidence/final/reconnect-selection.json` |
| 页面、介绍与录像 | 按独立媒体记录核对 | 普通登录、任务深链、历史诊断、证据、故障时拓扑及事件操作；12 张截图、6 页介绍 PDF 和 270 秒中文配音演示视频的实际核验入口为 `evidence/ui-final/voiced-media-qa.json` |
<!-- real-validation:end -->

原规格中的业务 OOM 正例、同 VM 争用各五次、真实 GPU、全新 Linux 的安装到卸载完整复现、完整平台断电冷恢复及公网 TLS / 轮换尚未由上述检查证明。R0、R2、R3 的验收范围与原 SC-001 四类场景分别判断，详见 [发布证据索引](release-evidence.md)。

## 从交付源码复现

1. 解压源码、独立模型与离线镜像归档，并核对外层和内部 SHA256。目录布局和导入入口见 [资产索引](assets-delivery-index.md)。
2. 按源码根目录 `README.md` 创建新的权限受限配置，再部署控制面、数据库、worker 与 Prometheus。默认 prod 配置关闭 lab。
3. 在授权业务 Linux VM 内部署探针、CPU 模型、Agent、工具及独立客户端，填写真实 VM UUID 和持久安装身份。每个终端沿用同一个项目名、env-file 和全部 Compose 覆盖文件。
4. 先运行正常业务，达到至少 30 个同依赖路径成功样本；任务总耗时与任务内模型累计耗时基线分别核对。超时不计为正常成功样本。
5. 按 [UI 操作指南](ui-operation-guide.md) 查看已完成的精确记录，或独立运行新编号的故障实验。CPU 参数为 0.1 核、180 秒；其他场景的时长和串行请求数从实际通过的 manifest 读取。
6. 分别核查原配置恢复、新业务任务成功、全部相关告警恢复、来源新鲜与稳定窗口。原始失败和注入前中断尝试保留，不计完整通过。
7. 需要卸载时使用 `deploy/uninstall.sh`；按实际部署范围撤销并验证旧凭据拒绝，默认保留数据。全新环境的整段安装—故障—恢复—卸载仍需独立运行和保存记录。

本机 UI 的 Windows 启动、普通登录、历史轮次选择、证据和故障时拓扑、确认与静默见 [详细操作指南](ui-operation-guide.md)。录像为已完成实验的 UI 回看；已完成配音的逐镜头旁白见 [口播稿](video-narration.md)。GitHub 仅发布源码，视频及配音素材保存在本地。源码访问核验见独立发布记录，比赛邮件尚未发送。

已创建的公开仓库为 [chennaji9/LayerTrace](https://github.com/chennaji9/LayerTrace)。源码 ZIP 与 bundle 采用同一脱敏快照；仓库提交和未登录可见性以独立发布记录核对。打包 manifest 中外部发布字段只代表创建本地包时的状态。
