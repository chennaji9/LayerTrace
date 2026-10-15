# 测试与交付核验报告

执行日期：2026-10-15，Asia/Shanghai。控制面、worker 与浏览器路径的核验结果如下。自动化使用独立测试数据库，空库冷启动使用临时容器与网络；测试没有访问业务 VM，也没有对演示数据库运行清表夹具。

## 当前源码核验入口

| 检验 | 方法与环境 | 结果 | 原始证据 |
|---|---|---|---|
| 契约、单元、集成、场景、安全与性能 | Python 3.12；项目 `.venv`；独立 PostgreSQL 16.15 / Ubuntu | 364 passed，157.69 秒 | [tests-core-r4.log](evidence/control-plane-acceptance/r4/tests-core-r4.log.txt) |
| 浏览器端到端 | 交付源码；独立测试库；普通表单登录与签名会话 | 20 passed，262.67 秒 | [tests-e2e-r4.log](evidence/control-plane-acceptance/r4/tests-e2e-r4.log.txt) |
| 历史诊断浏览器专项（历史记录） | 独立 pytest 会话；历史轮次交互及相关回归 | 5 passed，140.22 秒；另有单项流程 1 passed，53.03 秒；单列不累计 | [tests-ui-history-regression.log](evidence/control-plane-acceptance/tests-ui-history-regression.log.txt)、[tests-ui-history.log](evidence/control-plane-acceptance/tests-ui-history.log.txt) |
| 全仓静态检查 | Ruff 0.6.8；项目规则；交付源码 | All checks passed | [ruff-r4.log](evidence/control-plane-acceptance/r4/ruff-r4.log.txt) |
| PostgreSQL 16.4 空库冷启动 | 固定控制面镜像；Alembic 迁移；正式 API 与 worker；`MODE=prod` | 49 项检查通过；临时资源清理通过 | [cold-smoke/report.json](evidence/control-plane-acceptance/r4/cold-smoke/report.json) |
| Compose 默认配置 | 随机凭据；离线覆盖；prod 默认；loopback 入口与 TLS 要求 | 12 项检查通过 | [deploy-defaults-evidence.json](evidence/control-plane-acceptance/deploy-defaults-evidence.json) |
| 镜像与运行源码一致性 | 应用、探针、Agent、集成、规则、指定部署/打包文件及原始许可逐一比对 SHA256 | 152 个指定文件匹配 | [image-source-parity.json](evidence/control-plane-acceptance/r4/image-source-parity.json) |
| 控制面离线镜像导入 | 归档大小/SHA256、config 身份与具名引用核验 | 3 个具名引用核验通过 | [cp-images-load.json](evidence/control-plane-acceptance/r4/cp-images-load.json) |
| 非空 gap 心跳序列化及既有契约 | 独立单元/契约会话，非空时间字段按 JSON 格式入库 | 8 passed，10.96 秒，其中序列化专项 3 项；单列不与 Core 累计 | 材料 `evidence/heartbeat/heartbeat-gap-fix-review.json`、`heartbeat-gap-regression-fixed.log` |

本机启动恢复另有 6 项定向测试通过（0.91 秒），覆盖平台 API 启动等待、超时及 VM 过渡状态；对应部署脚本与测试文件的 Ruff 检查通过。该会话单独记录，不称完整套件重新执行。

Core 与 E2E 使用两个独立 pytest 会话。浏览器专项检查的范围与完整 E2E 存在重合，独立保留原始结果，不与 20 项 E2E 重复累计。

日志链接使用字节相同的 `.log.txt` 副本，便于在解压源码后离线查看。原始 `.log` 保留于材料证据，副本与原件的大小和 SHA256 对照见 [交付日志副本清单](evidence/control-plane-acceptance/r4/log-text-copies.json)；独立历史专项另见 [专项日志副本清单](evidence/control-plane-acceptance/log-text-copies.json)。

交付控制面和worker的OCI身份为 `sha256:18803bb39692e0b1d9d739233dcd84485a71c59b38814f809786679ab22bd616`，标签为 `layertrace-control-plane:delivery-20261015-r4`。152个指定文件的镜像/源码指纹和3个具名引用的实际导入已经核验；Core 364 项、E2E 20 项、空库 49 项通过，原始日志见 [控制面交付验收证据](evidence/control-plane-acceptance/r4/README.md)。152文件范围不包含测试与一般说明文档。本机控制面使用WSL虚拟环境web/worker；探针复用13模块与两容器代次证明、Agent/工具9文件证明、独立客户端历史build/load记录分别保留其验证范围。

真实运行的非空 gap 心跳曾返回 HTTP 204；历史 dropped 313 和 gap 仍可见。最终四类来源及队列状态，以材料 `evidence/final/environment-observed-final-delivery.json` 为准。队列清空不能替代全程无丢失结论。

## 已核验的行为

身份与拓扑检查覆盖完整容器ID、`CID@StartedAt`原始纳秒时间、实例代次、依赖有效期与历史拓扑。终结实例不依靠残留关系进入当前因果图；旧实例、旧依赖与故障重叠任务不能充当健康基线。R2 v1.0.2 的健康口径为任务内模型累计耗时（initial 与 synthesis累计），不是单阶段基准。真实单个闭合模型phase的duration_ms可逐条作为该累计量的保守下界比较，不跨phase或task求和；调用方不能借用模型耗时取得归因。实际关联窗口150秒；至少30个成功样本、严格两倍及500ms门槛保留。定向与独立离线案例证明契约边界，不能替代完整回归或真实故障验收；CPU12真实失败保持failed。

任务与证据检查覆盖阶段事实、`interrupted/inferred` 与客户端 `observed` 失败、同目标诊断支持链、反证和缺失项、原始证据权限与保留期提示、事件幂等及稳定游标分页。退出码 137 或 SIGKILL 只支持进程异常退出，不单独形成 OOM 结论。事件单保存不可变诊断修订，浏览器可选择历史轮次；恢复后的最新轮次不会遮蔽故障期结论。

浏览器验收覆盖页面导航、任务深链、历史轮次、证据和拓扑，以及确认、静默和恢复状态；桌面与移动响应式页面均有检查。普通表单登录建立 HttpOnly 签名会话，写操作校验 CSRF。机器 API 校验缺失/无效凭据、项目权限、探针注册、心跳和撤销；撤销返回 204，旧凭据心跳返回 403，同安装身份无法自动重新注册。

空库检查使用独立的PostgreSQL16.4、迁移、正式API与worker，核查任务投影、R0、证据关联、幂等与恢复。实际检查数、恢复等待时长、worker日志和临时资源清理结果，见镜像对应的 `cold-smoke/report.json`；49 项通过，60 秒稳定恢复门槛实等 60.79 秒，清理已核验。

## 真实实验的独立证据

真实 ZSvirt 平台接入、VM CPU 模型和故障实验具有各自的事实来源。以下结果来自原生故障、公开 API 和保存证据的独立重算；失败、超时和反证原件全部保留。

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

诊断证据强度与时间质量分别记录，不能将 high 等同于完全可信的时序。端到端耗时标明所选诊断轮次的原始时间及 time_confidence，降级记录按原值保留。任务 `duration_ms` 为 Agent 处理总耗时，客户端往返时间另行记录；独立批次 P95 与正式诊断保存的滚动模型基线分别核对。

历史探针窗口保留events 403后注册更新与成功上报、一次性客户端移除后inspect404等原始异常，分类与独立审阅在材料 `evidence/history/r3/final/observer-final-exception-classification.json` 和 `observer-final-exception-review.json`；原false不改写为没有异常。当前源码各阶段日志分别从 `evidence/final/overhead/observer-log-review-after-*.json` 核查，不能把历史已处理异常等同当前窗口无异常。

实验驱动源码按内容 SHA256 归档至材料 `evidence/runner-sources/`，具备实际镜像与源码身份绑定的驱动离线核验见 `evidence/runner/runner-source-binding-review.json` 和 `runner-source-binding-offline.log`。准备核验不替代真实故障结果；历史中断的原始 manifest 和恢复侧记录独立保留，不计为正式通过。完整需求与成功标准的已覆盖范围见 [发布证据索引](release-evidence.md)。

公开守卫的 13 组离线核验保存在材料 `evidence/runner/runner-in-flight-poll-offline.log`；包含原始告警选择器的 17 组综合审阅另见 `evidence/runner/raw-alert-selector-reviews/`，两组范围有重合、分别保留，各次运行采用 manifest 中记录的驱动 SHA256。注入前任务列表、详情以及请求和返回时间保存在每次 run 的 `in-flight-public-polls.json`；守卫轮次之间等待 0.5 秒，实际间隔还包含 API 响应耗时。历史引用补链侧记录保存在 `evidence/runner/context-reference-links/`，标记为未选历史上下文的引用不计为当前故障正式通过。

## 复验方法与材料范围

配置独立测试数据库后，在源码根目录的 WSL shell 中分别执行：

```bash
.venv/bin/python -m pytest tests/contract tests/unit tests/integration tests/scenario tests/security tests/performance -q
.venv/bin/python -m pytest tests/e2e -q
.venv/bin/python -m ruff check .
.venv/bin/python scripts/cold_smoke.py \
  --image layertrace-control-plane:delivery-20261015-r4 \
  --directory /absolute/new/cold-smoke-evidence
```

异步套件与 Playwright 同步 E2E 分开执行。`cold_smoke.py` 要求此前不存在的证据目录，并使用可丢弃的 PostgreSQL、worker 与临时网络。复验者应使用测试专用配置，避免清表夹具连接演示或业务数据库。

冷启动中的 Prometheus API 使用明确的空告警测试替身，证明 API、worker、诊断和恢复链路；真实 Prometheus 采样、ZSvirt 只读权限与 VM 业务分别查阅实际实验材料。此报告不宣称真实 GPU、公网 TLS/轮换、全新 Linux 的安装—完整故障—恢复—卸载复现或全部原规格成功标准已验收。

PostgreSQL16.15回归与16.4空库环境分别记录。经典Docker图像存储的兼容路径具有config digest核验与具名引用覆盖，尚未另起经典daemon实测。自动化与专项分别按原始日志核对，具有重合的会话不累计。所列核验不作生产就绪声明。
