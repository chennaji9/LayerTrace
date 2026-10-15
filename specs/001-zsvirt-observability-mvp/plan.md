# 实施计划：ZSvirt 全栈可观测诊断 MVP

**分支**：`001-zsvirt-observability-mvp` | **日期**：2026-09-21 | **规格**：[spec.md](./spec.md)

**输入**：`specs/001-zsvirt-observability-mvp/spec.md`，设计依据为 `docs/ZSvirt-MVP设计方案.md` v3。

## 摘要

实现一个“以虚拟机为边界、以故障事件为入口、以证据链为输出”的诊断工作台。首版采用
模块化单体控制面、单独后台 worker 和 VM 内探针，将 ZSvirt 平台、容器、指标、结构化事件、
Agent 链路与回放事实标准化后，按故障时有效拓扑生成带来源、反证、缺失证据和修订历史的诊断。
界面使用服务端模板和局部更新，所有页面及 CLI 通过同一版本化 API；先以回放数据点亮完整
工作流，再按迭代逐步替换为真实容器、指标、ZSvirt、AI 工作负载和故障数据。

## 技术上下文

**语言/版本**：Python 3.11+；YAML 用于声明式规则；HTML/CSS/JavaScript 仅用于服务端页面增强

**主要依赖**：FastAPI、Pydantic、SQLAlchemy、Alembic、Jinja2、HTMX、Cytoscape.js、
ECharts、Prometheus、node_exporter、Caddy、OpenTelemetry SDK/Collector；Tempo、Grafana、
Alertmanager/Webhook 与 NVML/DCGM 按能力可选

**存储**：PostgreSQL 保存资源、关系、事件、告警、事件单、诊断、任务与快照；Prometheus 保存
数值时序；Tempo 可选保存 Span；VM 探针使用有界本地 spool 保存待发送重要事件

**测试**：pytest 单元/契约/集成测试，HTTP 客户端契约测试，Docker Compose 场景测试，
Playwright 浏览器验收与截图，故障注入正例/反例和干净环境快速验证

**目标平台**：控制面与工作负载运行于 CPU-only Linux/ZSvirt Linux VM；WSL 仅作本地开发验证；
容器运行时首版为 Docker

**项目类型**：模块化单体 Web 应用 + 独立 worker + VM 探针 + CLI + 本地样例 AI 工作负载

**性能目标**：事件型故障 30 秒内形成可见诊断；持续阈值型故障 90 秒内形成可见诊断；
轻量探针空闲平均 CPU 初始目标低于 0.05 vCPU、RSS 低于 100 MiB，标准探针分别低于
0.1 vCPU 和 300 MiB，所有数值必须实测后才能对外声明

**约束**：离线可运行；不依赖外部闭源模型 API；生产默认关闭 lab 通道；ZSvirt 凭证只读；
来源全程可见；事实仅追加且幂等；后端失联显示 `unknown`/`stale`；单机最小预算为
4 vCPU、8 GiB 内存和 20 GiB 磁盘；无 GPU/平台账号时必须保留完整回放路径；跨主机入口
必须使用 TLS 并验证对端身份，仅 loopback 开发入口允许 HTTP；事件限制为单条 256 KiB、
单批 8 MiB/500 条、顶层属性 64 项和嵌套深度 8 层

**规模/范围**：单控制面、单项目、1 至 2 台真实 Linux VM；8 个主要页面；4 条首版诊断规则；
4 个必需真实故障场景；回放压测覆盖 10/50/100 台等价数据，但不替代真实规模测试

## 宪章检查

*门禁：Phase 0 前检查，并在 Phase 1 设计完成后复查。*

| 宪章要求 | Phase 0 前 | Phase 1 后 | 设计证据 |
| --- | --- | --- | --- |
| 证据先于结论 | 通过 | 通过 | 事实、证据、告警、事件单与诊断分表；诊断契约强制支持/反证/缺失项和规则版本 |
| 稳定身份与历史拓扑 | 通过 | 通过 | 外部身份唯一键、显式别名、关系有效期和 `topology?at=` 历史查询 |
| 来源透明与诚实降级 | 通过 | 通过 | 五类 provenance 为公共枚举；回放和真实数据共用写入及规则契约；状态契约包含 gap/stale/unknown |
| 最小权限与隐私默认保护 | 通过 | 通过 | 只读平台凭证、短期注册令牌、绑定范围写入、跨主机 TLS、受限 Docker 代理、入队前脱敏和 lab 隔离；安全测试拒绝明文与不可信证书 |
| 可验证的增量交付 | 通过 | 通过 | 9 个可见迭代，每轮有场景验证与截图；契约、单元、集成、反例和快速验证覆盖 |
| 模块化单体及存储边界 | 通过 | 通过 | 一个控制面代码库与一个 worker；事实、时序和 Span 按宪章分离，无消息队列和微服务拆分 |
| 单一事件单事实来源 | 通过 | 通过 | 控制面独占事件单状态；Prometheus 仅提供告警状态，通知系统仅作为输出 |
| 离线与降级能力 | 通过 | 通过 | ReplayProvider、本地开放模型和固定离线前端资产；无 GPU/ZSvirt 时仍可完成核心验证 |

门禁结果：无违反项，无需复杂度豁免；Phase 1 设计未引入新的宪章风险。

## 项目结构

### 本特性文档

```text
specs/001-zsvirt-observability-mvp/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── openapi.yaml
│   ├── events.schema.json
│   ├── scenarios.schema.json
│   ├── diagnostic-rule.schema.json
│   └── provider-contracts.md
└── tasks.md                 # 由后续 $speckit-tasks 生成
```

### 仓库源码

```text
apps/control_plane/
├── api/
├── diagnosis/
├── incidents/
├── ingestion/
├── inventory/
├── integrations/
├── web/
│   ├── templates/
│   └── static/vendor/
└── worker.py
agents/vm_probe/
packages/contracts/
integrations/
├── zsvirt/
└── replay/
rules/
deploy/
├── caddy/
├── node_exporter/
└── prometheus/
workloads/demo_agent/
scenarios/
fixtures/
tests/
├── contract/
├── unit/
├── integration/
├── scenario/
└── e2e/
docs/screenshots/
```

**结构决策**：使用原方案的模块化单体布局。`apps/control_plane` 负责业务编排、API、页面与单一
事件单状态，`worker.py` 串行执行平台轮询、告警同步与诊断任务；探针、平台适配器、公共契约、
规则、部署资产、样例工作负载和场景按独立边界组织，但不拆成独立微服务产品。

## 实施阶段

| 迭代 | 目标 | 可见验收 | 进入下一轮的门禁 |
| --- | --- | --- | --- |
| 0 | 仓库骨架、Compose、8 页导航、`/status`、许可证准入与环境风险验证 | 一条命令启动；生成环境风险记录；状态页如实显示数据库、指标和 ZSvirt 能力 | 确认平台指标/告警、VM CPU 上限、guest steal、网络方向、跨主机 TLS 入口及依赖许可证兼容性 |
| 1 | 数据模型、写入 API、ReplayProvider、当前拓扑、规则骨架与 R1 | OOM 回放产生当前拓扑、事件单和诊断 | 重建后结果一致；137 非 OOM 反例通过；页面不读剧本答案 |
| 2 | 探针注册、真实容器发现、事件与限制快照 | 真实容器和限制出现在页面 | 同名容器不合并；重建关闭旧关系 |
| 3 | VM/容器真实指标和查询适配层 | CPU、内存、throttling 图表更新 | 与运行时观测基本一致；断采显示未知 |
| 4 | ZSvirt 发现、事件、可用指标与 VM 身份绑定 | 平台宿主机、VM、告警和能力清单可见 | 实际接口/权限形成文档；平台断开显示陈旧 |
| 5 | 本地模型、Agent、工具服务、负载发生器与可选 Span | 真实任务显示分段耗时和精确实例关系 | 离线任务成功；30 分钟流量形成有效基线 |
| 6 | R1 真实数据闭环、事件单聚合与 A1 实验 | 注入后 30 秒内出现 OOM 诊断并可恢复 | 连续 5 次一致；推断与客户端观测互证 |
| 7 | R2/R3/R4、B/C/D1、告警运营和降噪 | 相似延迟得到不同诊断；静默与降噪可见 | 四个必需场景各 5 次一致；无关系错误不合并 |
| 8 | 安全、可靠性、开销、干净环境和交付加固 | 状态页展示实测开销；完整复现和视频完成 | 敏感信息零泄漏；模拟内容全标源；交付清单齐全 |

第 15 个工作日结束时若迭代 6 尚未完成，立即执行原方案止损：先移除 Tempo、A2、D2、Webhook
和历史时间滑块 UI，再将 D3 改为回放并合并 B/D1 注入流程。A1、C、至少一个真实资源瓶颈、
ZSvirt 身份与告警接入、诊断详情、事件单运营、回放规则引擎和可复现材料不得删除。

## 复杂度跟踪

无宪章违反项。新增 Caddy 只承担控制面跨主机入站 TLS 终止、证书校验和 HTTP 到 HTTPS 拒绝策略，
收益是集中满足探针、Prometheus remote write 和浏览器入口的传输安全要求；Prometheus pull 由
node_exporter TLS 配置保护，ZSvirt 出站连接由适配器验证平台证书。成本是一项固定版本镜像、
证书挂载与轮换流程。已评估由各 Python/Prometheus 进程分别终止 TLS 的更少组件方案，但会复制
证书配置和轮换逻辑，因此未采用。loopback 开发流量仍可直接访问控制面，不强制引入代理。
