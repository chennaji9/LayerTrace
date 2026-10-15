# 发布证据索引

核验日期：2026-10-15，Asia/Shanghai。LayerTrace 已在 ZSvirt 管理的 Linux 业务 VM 中运行只读探针、CPU 模型、Agent、HTTP 工具和独立客户端；控制面通过公开 API 和正式 worker 消费真实事实。控制面自动化、空库冷启动、固定离线资产和许可按独立原始记录核对。正式故障、探针开销、断线补传及演示材料的状态，以实测汇总表和对应原始记录为准。

`PASS` 表示表中注明的范围已有验证证据，`PARTIAL` 表示已有实现或部分验证，`UNVERIFIED` 表示未执行所注明的外部验证。自动化通过不代表全部目标环境指标达标，真实正常任务也不代表故障诊断已成立。

## 实际环境与证据入口

| 项目 | 已核验事实 | 证据与边界 |
|---|---|---|
| 平台与 VM | 平台 GetVersion 实际返回 ZSvirt 1.0.0；host 为 Enabled/Connected；Ubuntu 24.04.5 业务 VM，2 vCPU、3 GiB、20 GiB | [实际环境](environment-risk.md)；材料包 `evidence/environment.json` 与 `environment-observed-diagnostic-baseline.json`；源 qcow2 标识另记为 1.0.0-h84r |
| CPU 身份 | QEMU/KVM、host-passthrough、guest 可见 AVX2；两个 vCPU 各有一核配额，父 cgroup 不限制总配额 | 材料包 `evidence/virtualization-performance.json`；完整平台预算另计 |
| 只读平台接入 | Normal 观察账户可查询 host、VM、原生告警/事件；同名 VM 写请求被 SYS.1019 拒绝 | [权限机制](zsvirt-readonly.md)；材料包 `evidence/readonly-platform-native.json` |
| CPU AI 业务 | Qwen2.5-0.5B-Instruct Q4_K_M，llama.cpp build 11610；本地模型、工具及摘要真实执行 | [模型清单](../deploy/models/manifest.yaml)；正常任务原始 JSONL 与任务 API |
| 独立正常业务样本 | 35/35 成功；任务内累计模型 P95 25,843 ms，任务处理总耗时 P95 26,393 ms，工具 P95 853 ms。R2 另使用所选诊断保存时刻的健康滚动基线。 | 材料包 `evidence/final/baseline-business.jsonl`、`baseline-summary.json`、`baseline-client.json`、`before-formal-readiness.json`；共享宿主压力快照保留，不作独占宿主性能基准；任务处理为Agent处理总耗时，客户端往返时间另记；诊断采用自身保存的滚动模型基线 |
| 业务 VM 内核与恢复 | 实际启动内核 7.0.0-38-generic；资源预算、SSH 和业务恢复由最终环境检查确认；内核异常与重启调查原始记录保留 | 材料包 `evidence/final/environment-observed-final-delivery.json`、`kernel-update.json`、`kernel-update.log`、`guest-kernel-recovery.json`、`kernel/reboot-investigation/` 及控制台截图；`kernel-final-environment.json` 是内核启用窗口的历史检查 |
| 独立 OOM 反例 | 无依赖关系的 32 MiB 测试容器发生真实 OOM；并发正常业务成功；未被关联成业务的 high OOM 原因 | 材料包 `evidence/negative-cases/independent-oom-final.json`；此记录证明反例，不替代业务 OOM 正例 |
| 本机恢复 | 网络安装和隧道启动的重复执行保留匹配进程、容器身份及已有 NAT 规则 | [恢复入口](../deploy/local-lab/README.md)；材料包 `evidence/local-recovery-check.json`；完整平台断电冷恢复未由此推定 |
| 分发资产 | GGUF、CPU 推理镜像、探针/业务镜像及控制面核心离线归档有固定身份与 SHA256；原始许可随资产保留 | [资产索引](assets-delivery-index.md)、[第三方声明](../THIRD_PARTY_NOTICES.md) |

材料包中的 `evidence/` 指白名单导出的实际证据，不含运行凭据、平台磁盘、SSH 私钥或完整 env。控制面交付验收说明见 [control-plane-acceptance/r4/README.md](evidence/control-plane-acceptance/r4/README.md)，其日志、报告与精确镜像清单一并保存到材料证据目录。本机控制面为WSL虚拟环境的web/worker；交付容器镜像完成独立导入和空库检查。

历史 HWE 采集文件只有 10 个完成且成功的任务，窗口被非主动 guest 重启打断，未达到至少 30 个成功样本的门槛。`baseline-hwe-summary.json` 标记为 interrupted、baseline_eligible=false；重启原因尚未确认。此窗口和此前基线均作为历史原始记录保留，不用于正式 CPU 对照。进程退出实验中合法重启 Agent 形成新 StartedAt，各次证据按当时实例身份核对。

## 控制面与浏览器验收

| 检验 | 结果 | 范围 |
|---|---|---|
| 契约、单元、集成、场景、安全与性能测试 | 364 passed，157.69 秒 | Python 3.12，独立 PostgreSQL 16.15 测试库；不对演示数据库运行清表夹具 |
| 浏览器端到端 | 20 passed，262.67 秒 | 普通表单登录、签名会话、页面导航、任务深链、历史诊断轮次、证据、拓扑、确认与静默；含桌面/移动响应式页面 |
| 全仓 Ruff | All checks passed | 交付源码及固定项目规则；[Ruff 日志](evidence/control-plane-acceptance/r4/ruff-r4.log.txt) |
| 固定镜像与运行源码 | OCI 18803bb3…；152个指定文件的SHA256一致 | 应用、探针、Agent、集成、规则、指定部署/打包文件及原始许可；测试与一般说明文档不在该一致性范围；[交付验收记录](evidence/control-plane-acceptance/r4/README.md) |
| PostgreSQL 16.4 空库冷启动 | 49 项检查通过，临时资源及凭据清理已核验 | Alembic 到 0002、正式 API、worker、任务投影、R0、证据权限、会话 CSRF、幂等、撤销与60秒稳定恢复门槛的实际等待记录 |
| Compose 默认配置 | 12 项检查通过 | prod、lab 关闭、随机凭据、loopback 入口、TLS 要求与离线 pull never |
| 可选 ZSvirt 私有 CA | 只读挂载与非 root 读取/解析通过 | 独立临时容器、UID 10001；不替代真实公网 TLS 入口或证书轮换实验 |
| 资产配套文件保护 | 最终资产核验逐项记录归档、精确镜像清单、来源与原始许可；错误类型、凭据伪装及摘要不符输入具有反例检查 | 材料包 `evidence/final/delivery-assets-verification.json`，实际绑定最终OCI和四份精确镜像清单；打包扫描实际输入及受保护的 VM 已知秘密 |
| 非空数据缺口心跳 | JSON 序列化专项与既有契约共 8 passed，10.96 秒；真实心跳 HTTP 204，四来源 healthy、当前队列 0 | 材料包 `evidence/heartbeat/heartbeat-gap-fix-review.json` 与专项日志；历史 dropped 313 及 gap 保留，不声明采集全程无丢失 |
| 心跳专项历史验收记录 | OCI e17d971…；152 个指定文件一致；独立空库冷启动 49 项通过、清理已核验；离线归档 4 个具名引用导入通过 | [心跳专项镜像证据](evidence/control-plane-acceptance/heartbeat/README.md)；保留原始身份和结果，不重复累计交付套件数量 |

冷启动的 Prometheus API 使用明确的测试替身；真实 Prometheus 采样、平台只读接入和 VM 业务证据分别来自实际实验环境。UI 操作指南和口播稿是交付说明，实际录屏及最终页面验收记录另行汇总。

## 真实故障与可靠性汇总

以下结果来自已完成的实验、精确身份与原始时间核对及恢复验证。未达预期的运行保留原始 manifest，不删除失败、超时或反证。诊断只接受引用同目标原始事实的同一轮次结论，历史 peak 与最新修订分别记录。

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

CPU 实测记录保留逐轮 `time_confidence`。high 代表诊断支持强度，时间质量须独立核查；耗时统计按所选轮次标注，不将降级轮次称为完全可信的时序。正式批次的独立审阅入口为材料 `evidence/final/cpu-formal-batch-offline-audit.json` 和 `native-formal-batch-offline-audit.json`，原始 manifest 与诊断不改写。

探针基线窗口的两次已处理HTTP异常及恢复链保存在材料 `evidence/final/observer-final-exception-classification.json` 和独立的 `observer-final-exception-review.json`：events403之后出现注册、凭证更新和成功上报；一次性客户端自动删除引发inspect404，随后库存与事件上报恢复。泛Traceback扫描的false原值保留；这些记录不证明403成因或采集全程无缺口，也没有负sleep、SQLite异常或自动退出证据。各阶段日志复核、合法部署与重启生命周期单独保存。

本机R2基线门槛为至少30个成功样本，健康样本窗口3600秒；统计口径为任务内模型累计耗时（initial与synthesis累计）。关联证据窗口为150秒。当前单个闭合模型phase的真实duration_ms可逐条作为累计量的保守下界比较，不跨phase或task求和；延迟必须严格大于健康P95两倍且绝对增加严格大于500ms。裸 CPU 限流信号和跨目标的配额告警不能阻止模型健康基线建立。恢复同时要求原配置已核验、新业务成功、归属告警恢复、关联来源新鲜及稳定窗口；正式默认 60 秒，本机演示 15 秒。最终真实参数以受保护配置的非敏感记录和每次 manifest 为准。

## 功能需求映射

| 需求 | 状态 | 证据与明确范围 |
|---|---|---|
| FR-001 | PASS | 回放与真实 ZSvirt/VM 路径具有独立来源；无 GPU 时显示能力缺失，不声明硬件实测 |
| FR-002 | PASS | 8 类页面与桌面/移动 E2E；最终实机截图见 UI 汇总 |
| FR-003–005 | PASS | 库存/身份/有效期拓扑契约、实例代次及依赖建立时间回归；真实 VM/完整 CID/StartedAt 记录 |
| FR-006–008 | PASS | 追加事实、发生/观测时间、幂等、EventReceipt 水位与 unresolved 状态；库存与任务链回归 |
| FR-009–014 | PASS | 公开 API、真实任务分段、进程中断、支持/反证/缺失项及不可变诊断修订；历史轮次浏览器验收 |
| FR-015–016 | PASS | R1/R2/R3/R4 必要证据与反例自动化、R0 进程规则、30 样本及 P95 门槛；真实场景独立验收 |
| FR-017–020 | PASS | 告警/事件单/通知分离、聚合、确认、静默与稳定恢复自动化；确认和静默保留事实 |
| FR-021–023 | PASS | 陈旧/失联/未知/缺口、平台能力和权限可见；host/VM/原生告警真实接入，未支持字段明确缺失 |
| FR-024 | PASS | 真实 CPU 离线模型、工具与摘要链路有成功任务记录；固定模型/推理身份及分段事实；独立正常样本与当轮健康基线单列汇总 |
| FR-025 | PARTIAL | 原生实验 CLI 覆盖 R0/R2/R3，各 5 次真实故障及恢复通过；原规格的业务 OOM 正例与同 VM 争用未由三类实验替代 |
| FR-026–027 | PASS | 实验标签、完整目标 ID、最长 180 秒、配置保存、独立 watchdog/手工恢复；prod 默认禁止实验写入 |
| FR-028 | PARTIAL | 真实平台读权限与写拒绝、SSH 加密隧道、会话/CSRF 自动化和可选 CA 挂载；公网 TLS/轮换未实测 |
| FR-029–030 | PASS | 队列前正文排除/脱敏、边界大小拒绝、身份保真与导出扫描；仅在业务结果文件保存模型正文 |
| FR-031 | PARTIAL | SQLite 整批 ACK、持久队列、真实 SIGKILL 恢复及丢弃/gap 回归；60 秒 VM 断联与原探针重启补传通过 |
| FR-032–034 | PASS | 来源状态、机器接口、可选硬件/平台能力及无闭源模型 API 的回放路径；离线控制面空库检查 |
| FR-035 | PASS | 配置生成/卸载回归、撤销 204、旧心跳 403 与安装身份 tombstone；默认保留数据，删除卷独立确认 |
| FR-036 | PARTIAL | 实际平台/权限/依赖/许可/容量/安全有记录；开销、逐轮故障时延与补传结果见对应实测报告 |
| FR-037 | PASS | 单控制面、单项目、独立 worker、1 台业务 VM；多租户/高可用/自动修复未声明 |
| FR-038 | PASS | GPU 回放要求可信映射与直接 CUDA OOM，高利用率反例不归因；真实 GPU/vGPU 为 UNVERIFIED |

## 成功标准映射

| 标准 | 状态 | 证据与明确范围 |
|---|---|---|
| SC-001 | PARTIAL | 原规格要求 OOM、CPU、工具、同 VM 争用四类各 5 次；R0/R2/R3 汇总不等同全部四类验收 |
| SC-002 | PARTIAL | 真实逐轮时延及统计见 detection-latency.md；目标是否达到按实际值核对 |
| SC-003 | PASS | 跨 VM 同名、重建代次、迟到事实和依赖生效时间自动化；本机仅 1 台业务 VM，不声明双 VM 实机部署 |
| SC-004 | PASS | 重复事实/消费、聚合、静默与恢复契约及冷启动 API 幂等；正式补传记录单独归档 |
| SC-005 | PARTIAL | R2/R4 及无拓扑反例自动化、真实独立 OOM 不误关联；CPU 与邻居争用各 5 次实机区分尚未完成 |
| SC-006 | PARTIAL | 137 非 OOM、中断/客户端失败自动化及真实独立 OOM 反例；不证明同业务 OOM 正例的任务闭环 |
| SC-007 | PASS | staleness、时钟、迟到/缺失、代次约束和不可变修订自动化；实际断线状态依实测汇总 |
| SC-008 | PARTIAL | 真实 SQLite 强杀后恢复、ACK 删除及补传/gap 自动化；60 秒 VM 网络中断与原探针重启补传完成 |
| SC-009 | PARTIAL | 启用/暂停各 900 秒 A/B 已测量；不能直接称为原规格“轻量档/标准档”两个完整部署档均已实测 |
| SC-010 | PARTIAL | PostgreSQL 16.4 空库/独立镜像启动已通过，ZSvirt VM 有正常业务；全新 CPU Linux 的安装—真实故障—恢复—卸载完整复现未由空库检查推定 |
| SC-011 | PARTIAL | 泄漏、越权、明文/证书拒绝自动化与许可/清单输入防护通过；真实公网证书链/轮换未验证 |
| SC-012 | PASS | 无 GPU/平台账户的回放、核心页面、事件单生命周期及来源展示自动化 |

## 交付与能力边界

源码 ZIP 和独立 Git bundle 来自同一脱敏源码快照；材料、模型权重和离线镜像分别归档，每个包及内部文件都有大小与 SHA256。实验驱动源码保留于材料 `evidence/runner-sources/`，文件名中的 SHA256 必须与内容一致；实际镜像与源码身份绑定的离线核验见 `evidence/runner/runner-source-binding-review.json` 和 `runner-source-binding-offline.log`。原始正常窗口、失败和中断 manifest 全部保留；独立正常任务批次按实际成功数保存业务原件、代次与独立审阅，各次正式运行采用同轮诊断引用的健康基线。`--require-submission` 检查源码、PDF、180–300 秒视频、环境/场景证据、资产清单和未填写实测标记，不能代替内容审核或原规格合规判定。公开仓库为 [chennaji9/LayerTrace](https://github.com/chennaji9/LayerTrace)；GitHub 仅发布源码；视频、PDF 与其他材料仅本地交付，源码访问核验与比赛邮件状态按独立记录核对，`release-manifest.json` 外部状态字段保留本地打包时快照。

公开守卫离线核验见 `evidence/runner/runner-in-flight-poll-offline.log`，实际轮询记录在各次 run 的 `in-flight-public-polls.json`。历史引用补链侧记录位于 `evidence/runner/context-reference-links/`，原始 manifest 和 API 捕获保持原值；未选历史上下文不计为当前故障的正式通过证据。注入前失败且没有实际 run ID 的尝试保留为失败记录，不计有效故障实验次数。

本次是 Windows/WSL2 中的嵌套 KVM 实验，控制面和平台共享宿主资源，业务 VM 为真实 Linux guest。无真实 GPU；平台存储、管理节点引用可保持 unresolved；平台利用率、quota 与 GPU 映射未作为平台适配能力接入。host-passthrough 迁移兼容性、宿主 swap 与 CPU 竞争会影响尾延迟。模型单线程不代表 VM 只有一核预算，业务 VM 预算也不代表整套平台预算。

R0 进程退出与 R1 OOM 必须分别判定。配置恢复、CLI 返回和新任务成功不能分别单独替代完整恢复证据。只有直接关联的真实事实能够支持实机诊断；回放/模拟、未知、过期证据和未覆盖场景均保持可见。生产部署、完整平台断电恢复、业务 OOM 正例、同 VM 争用五次一致性、真实 GPU、公网 TLS 及跨环境性能外推不在当前已验证范围内。
