---

description: "ZSvirt 全栈可观测诊断 MVP 的依赖排序实施任务"
---

# 任务：ZSvirt 全栈可观测诊断 MVP

**输入**：`specs/001-zsvirt-observability-mvp/` 下的 `spec.md`、`plan.md`、`research.md`、
`data-model.md`、`contracts/` 与 `quickstart.md`

**测试要求**：规格和项目宪章明确要求契约、单元、集成、反例、场景与浏览器测试；每个用户故事
先完成测试任务并确认失败，再开始对应实现。

**组织方式**：共享基础设施只放在 Phase 1/2；其余任务严格按 7 个用户故事组织，确保每个故事
具有独立验收路径。所有命令默认在 WSL/Linux 执行。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可与同阶段其他 `[P]` 任务并行，且修改不同文件
- **[USn]**：对应 `spec.md` 的用户故事编号
- 每项任务均包含明确文件路径

## Phase 1：项目初始化与共享骨架

**目标**：建立模块化单体、独立 worker、VM 探针、部署与测试目录，不实现具体用户故事。

- [x] T001 按 `plan.md` 创建 `apps/control_plane/{api,diagnosis,incidents,ingestion,inventory,integrations,web/templates,web/static/vendor}`、`agents/vm_probe`、`packages/contracts`、`integrations/{zsvirt,replay}`、`rules`、`deploy`、`workloads/demo_agent`、`scenarios`、`fixtures`、`tests/{contract,unit,integration,scenario,e2e}` 与 `docs/screenshots` 包结构
- [x] T002 在 `pyproject.toml` 配置 Python 3.11+、FastAPI、Pydantic、SQLAlchemy、Alembic、Jinja2、HTTP 客户端、CLI、pytest、类型检查和格式化依赖并锁定版本
- [x] T003 [P] 在 `apps/control_plane/config.py` 和 `.env.example` 定义 `MODE=prod` 默认值、数据库/指标/平台连接、TLS 证书路径、可信代理、保留期、单事件 256 KiB、单批 8 MiB/500 条、属性 64 项、嵌套 8 层、spool 上限与可选组件开关，禁止默认启用 lab
- [x] T004 [P] 在 `apps/control_plane/main.py`、`apps/control_plane/worker.py` 和 `apps/control_plane/cli.py` 建立 API、单 worker 与 `layertrace` CLI 入口
- [x] T005 [P] 在 `deploy/Dockerfile.control-plane`、`deploy/Dockerfile.probe` 和 `deploy/Dockerfile.demo-agent` 创建非 root、固定依赖版本的镜像构建文件
- [x] T006 在 `deploy/compose.yaml`、`deploy/caddy/Caddyfile` 和 `deploy/caddy/README.md` 编排 control-plane、worker、PostgreSQL、Prometheus、Grafana、固定版本 Caddy 及可选 Tempo/Collector；Caddy 只暴露跨主机 HTTPS 并挂载可轮换证书，control-plane 的 HTTP 端口仅绑定 loopback，设置健康检查并保持 control-plane 与 worker 共享代码但进程独立
- [x] T007 [P] 在 `deploy/prometheus/prometheus.yml` 和 `deploy/prometheus/rules/` 配置 10 秒抓取、10 至 15 秒规则评估及受控标签，禁止把 `task_id`、`trace_id`、提示词作为指标标签
- [x] T008 在 `LICENSE`、`docs/license-policy.md` 和 `THIRD_PARTY_NOTICES.md` 先确定项目许可证、允许引入的许可证 allowlist 与再分发规则，再将通过准入的固定版本 HTMX、Cytoscape.js 与 ECharts 离线资产放入 `apps/control_plane/web/static/vendor/`
- [x] T009 [P] 在 `pytest.ini`、`tests/conftest.py` 和 `tests/e2e/conftest.py` 配置 PostgreSQL 测试库、Compose 场景、Playwright 桌面/移动视口和来源断言夹具
- [x] T010 在 `deploy/preflight.sh`、`docs/environment-risk.md` 和 `README.md` 建立只读环境预检与文档骨架，记录 ZSvirt 指标/告警字段、VM CPU 上限、guest steal、中心到 VM 网络方向、跨主机 TLS 入口及权限结论；未完成记录前禁止选择 pull/remote-write 或实现平台字段映射，且禁止把设计目标写成已验证事实

**检查点**：目录、依赖和 Compose 骨架就绪，可进入共享基础实现。

---

## Phase 2：阻塞性共享基础

**目标**：完成所有故事共用的契约、数据层、安全边界、API 外壳和 UI 外壳。

**关键门禁**：Phase 2 未完成前不得开始任何用户故事实现。

- [x] T011 [P] 在 `tests/contract/test_json_schemas.py` 为 `events.schema.json`、`scenarios.schema.json`、`diagnostic-rule.schema.json` 编写有效/无效样例测试，覆盖 replay 缺 scenario、fault 缺 inject/restore 与未注册 evaluator
- [x] T012 [P] 在 `tests/contract/test_openapi.py` 校验 `specs/001-zsvirt-observability-mvp/contracts/openapi.yaml` 的 16 条路径（含 `/api/v1/workloads`）、认证、loopback HTTP/跨主机 HTTPS 声明、4xx 响应和 Schema 引用
- [x] T013 [P] 在 `tests/integration/test_migrations.py` 先写迁移升级/降级、唯一约束、外键、有效期和枚举约束测试
- [x] T014 在 `apps/control_plane/db.py` 和 `apps/control_plane/models/base.py` 建立异步会话、事务边界、UTC 时间与不可复用 UUID 基类
- [x] T015 [P] 在 `apps/control_plane/models/resource.py` 实现 Resource 与 ResourceAlias：`kind` 支持 `project/host/vm/container/service/service_instance/gpu/vgpu`；唯一键严格为 `(tenant_id, provider, provider_instance, kind, source_id)`；`display_name` 不参与去重；`first_seen` 不得晚于 `last_seen`；同一 namespace/external_id 同一时刻只能指向一个资源
- [x] T016 [P] 在 `apps/control_plane/models/relationship.py` 实现 Relationship：`from_id != to_id`、`valid_to` 为空或晚于 `valid_from`、同一关系三元组有效区间不得重叠，并限制为 `data-model.md` 列出的 8 类有向关系
- [x] T017 [P] 在 `apps/control_plane/models/event.py` 实现 Event 与 Evidence：`event_id` 全局唯一；Event 仅追加并保留 `resource_ref/occurred_at/observed_at/source/provenance`；`resolution_state` 仅为 `resolved/unresolved/rejected`；`replayed` 必填 scenario 名称与偏移；Evidence 原始数据过期后保留摘要并设置 `source_expired=true`
- [x] T018 [P] 在 `apps/control_plane/models/task.py` 实现 TaskRun 与 ResourceSnapshot：`task_id` 项目内唯一；状态只允许 `running -> succeeded/failed/timed_out/interrupted`；终态不可回退；耗时非负；推断中断必须保存 `trigger_event_id`；同资源/时刻/config_hash 的快照去重
- [x] T019 [P] 在 `apps/control_plane/models/incident.py` 实现 Alert、Incident 与 Silence：Alert 状态仅 `pending/firing/resolved`；Incident 指纹项目内唯一且状态为 `open/acknowledged/resolved`；`alert_count >= 1`；Silence matcher 仅允许 fingerprint 或 `rule_id/resource_id/severity` 组合且只影响通知
- [x] T020 [P] 在 `apps/control_plane/models/diagnosis.py` 实现 Diagnosis：同一 Incident 的 revision 从 1 单调递增且唯一；候选必须含 `high/medium/low` 支持等级；支持/反证/缺失数组字段不可省略；观测影响与潜在影响分离；分数不得命名为概率；记录规则版本、时间可信度与上下文截断
- [x] T021 [P] 在 `apps/control_plane/models/source.py` 实现 SourceStatus 与 IngestionCursor：状态仅 `not_configured/healthy/degraded/stale/unavailable/permission_denied`；批次数据与游标同事务提交；失败不得前移游标；保存权限缺失、gap、queue_lag、dropped_count
- [x] T022 [P] 在 `apps/control_plane/models/scenario.py` 实现 ScenarioRun：只允许 `data-model.md` 定义的状态转换；保存原始配置、deadline、验证与恢复结果；活动状态失败或超时必须进入 restoring
- [x] T023 在 `apps/control_plane/migrations/versions/0001_core_schema.py` 创建 T015-T022 全部表、索引、唯一约束和检查约束，并使 T013 通过
- [x] T024 [P] 在 `packages/contracts/schemas.py` 实现 OpenAPI/JSON Schema 对应的 Pydantic 输入输出模型，限制单事件 UTF-8 JSON 最多 256 KiB、单批最多 8 MiB/500 条、namespace 最长 128、external_id 最长 512、attributes 最多 64 项且嵌套最多 8 层，任一超限整批返回 413
- [x] T025 [P] 在 `apps/control_plane/auth.py` 和 `apps/control_plane/middleware/transport_security.py` 实现 bearer 身份、tenant/project 服务端注入、资源范围检查及跨主机 HTTPS 强制；只信任来自 Caddy 的转发协议头，拒绝明文和不可信传输上下文，禁止客户端覆盖 `tenant_id/project_id`
- [x] T026 [P] 在 `apps/control_plane/ingestion/redaction.py` 实现字段允许列表及 token、手机号、邮箱过滤，入队前排除提示词、完整回答、工具参数原文、Authorization、Cookie、API key 和完整环境变量
- [x] T027 在 `apps/control_plane/ingestion/repository.py` 实现仅追加幂等事件写入、重复/拒绝/未解析计数和单批事务，使用 `event_id` 去重
- [x] T028 [P] 在 `apps/control_plane/integrations/protocols.py` 实现 `InfrastructureProvider`、`RuntimeProvider`、`EvidenceQuery`、`DiagnosticRule`、`NotificationSink` 类型化协议及能力/健康结果对象
- [x] T029 [P] 在 `apps/control_plane/api/errors.py` 和 `apps/control_plane/api/pagination.py` 实现统一错误体、稳定游标分页和 1-200 条 limit 约束
- [x] T030 [P] 在 `apps/control_plane/diagnosis/rule_loader.py` 校验规则 YAML、绑定注册 evaluator 和精确版本，拒绝任意 Python/SQL 与未知 evaluator
- [x] T031 在 `apps/control_plane/worker.py` 实现单 worker 的平台轮询、告警轮询、诊断任务和超时/退避/抖动调度外壳，防止 Web worker 重复执行轮询
- [x] T032 [P] 在 `apps/control_plane/web/templates/base.html` 和 `apps/control_plane/web/routes.py` 建立总览、拓扑、工作负载、任务、事件单、诊断、状态、实验 8 个页面导航，页面只能调用 `/api/v1/*`
- [x] T033 [P] 在 `apps/control_plane/web/static/app.css` 和 `apps/control_plane/web/templates/components/provenance.html` 实现五类来源徽标、unknown/stale/gap 状态和稳定无重排布局
- [x] T034 在 `tests/integration/test_foundation.py` 执行迁移、认证范围、跨主机明文拒绝、载荷大小/深度 413、脱敏、事件幂等、规则配置拒绝和 8 页面外壳集成测试，确认 Phase 2 门禁通过

**检查点**：共享基础就绪，所有故事可以按下方依赖关系推进。

---

## Phase 3：用户故事 1 - 从失败任务获得跨层证据诊断（P1）🎯 MVP

**目标**：直接写入多层事实后生成可追溯 R1 诊断，不依赖回放执行器或真实探针。

**独立测试**：通过事件 API 写入任务失败、容器 OOM 与 VM 内存正常反证，得到包含候选、证据、反证、缺失项、影响和建议的诊断；只有 137 时 R1 不成立。

### 先行测试

- [x] T035 [P] [US1] 在 `tests/contract/test_incident_diagnosis_api.py` 编写事件批量写入、事件单详情和诊断修订响应契约测试
- [x] T036 [P] [US1] 在 `tests/unit/diagnosis/test_r1_oom.py` 编写 OOMKilled/cgroup 增量正例、仅 137 反例、VM 内存不足并列候选和缺客户端观测降级测试
- [x] T037 [P] [US1] 在 `tests/integration/test_cross_layer_diagnosis.py` 编写任务—实例—容器—VM—宿主机事实写入到 revision 1 的端到端失败测试

### 实现

- [x] T038 [P] [US1] 在 `apps/control_plane/api/events.py` 实现 `POST /api/v1/events:batch` 的 Schema 校验、认证范围、脱敏、幂等写入与 202 计数响应
- [x] T039 [P] [US1] 在 `apps/control_plane/diagnosis/context.py` 构建症状前 120 秒至后 30 秒的事件、任务、快照、告警和证据上下文，时钟偏差超过 5 秒时将时间可信度降级
- [x] T040 [US1] 在 `apps/control_plane/inventory/temporal_graph.py` 实现按故障时刻读取有效关系、限制深度/节点数并设置 `context_truncated`
- [x] T041 [P] [US1] 在 `apps/control_plane/diagnosis/evidence.py` 实现证据摘要、稳定 source_ref、查询参数、gap 与过期状态持久化
- [x] T042 [P] [US1] 在 `rules/R1-container-oom.yaml` 定义窗口、必要证据、反证、关系路径、建议和规则版本
- [x] T043 [US1] 在 `apps/control_plane/diagnosis/evaluators/r1_oom.py` 实现 OOM 直接证据、任务失败、VM 内存反证和仅 137 不成立逻辑
- [x] T044 [US1] 在 `apps/control_plane/diagnosis/task_interruption.py` 将实例退出时同实例未结束任务标为 `interrupted/inferred` 并引用退出事件，客户端观测迟到时只追加证据
- [x] T045 [US1] 在 `apps/control_plane/diagnosis/service.py` 实现候选排序、并列/证据不足、支持与反证、实际/潜在影响以及不可变 revision 生成
- [x] T046 [US1] 在 `apps/control_plane/incidents/event_alerts.py` 为 OOM 与任务失败生成 Alert，并按项目+规则+稳定故障对象创建最小 Incident
- [x] T047 [US1] 在 `apps/control_plane/api/incidents.py` 实现 `GET /incidents/{id}` 与 `GET /incidents/{id}/diagnoses`
- [x] T048 [US1] 在 `apps/control_plane/web/templates/diagnosis/detail.html` 实现时间线、候选、反证、缺失证据、两类影响、规则版本和建议视图
- [x] T049 [US1] 在 `tests/e2e/test_us1_diagnosis.py` 验证用户从事件单进入诊断页并看到可追溯 R1 结论与 137 反例

**检查点**：Setup + Foundation + US1 构成最小技术 MVP，可直接通过 API 验证核心诊断价值。

---

## Phase 4：用户故事 2 - 无平台回放完整诊断链路（P1）

**目标**：无 GPU、无 ZSvirt、无外部模型时，回放事实通过正式管线复现 US1，期望断言不进入产品输出。

**独立测试**：清库后运行 `story-oom` 并验证结果一致；运行 `story-exit-137-not-oom` 时 R1 不成立。

### 先行测试

- [x] T050 [P] [US2] 在 `tests/contract/test_scenario_api.py` 编写 run/verify/restore、ScenarioRun 状态及回放完成后当前拓扑响应的契约测试
- [x] T051 [P] [US2] 在 `tests/unit/replay/test_scenario_loader.py` 编写相对时间展开、引用解析、期望断言隔离和非法 action 拒绝测试
- [x] T052 [P] [US2] 在 `tests/integration/test_replay_diagnosis.py` 编写清库重跑一致性、同管线入库和 137 非 OOM 反例测试

### 实现

- [x] T053 [P] [US2] 在 `integrations/replay/provider.py` 实现与 InfrastructureProvider 相同的 capabilities/discover/read_events/read_metrics/health 契约，其中 discover 必须输出回放资源与当前有效关系供拓扑查询
- [x] T054 [US2] 在 `integrations/replay/loader.py` 按当前基准时间展开 `+0s/+12s` 偏移并为所有回放对象写入 `replayed`、剧本名和偏移
- [x] T055 [US2] 在 `apps/control_plane/integrations/scenario_service.py` 实现回放 run、资源/当前关系/事件经正式 API 写入、独立 verify 断言和重跑隔离，禁止把 expected_assertions 写入产品表
- [x] T056 [P] [US2] 在 `fixtures/story-oom.json` 创建 1 宿主机、1 VM、3 容器、任务、OOM、VM 正常反证及测试期望剧本
- [x] T057 [P] [US2] 在 `fixtures/story-exit-137-not-oom.json` 创建只有退出码 137、无可信 OOM 证据的反例剧本
- [x] T058 [US2] 在 `apps/control_plane/api/scenarios.py`、`apps/control_plane/api/topology.py`、`apps/control_plane/web/templates/topology/index.html` 和 `apps/control_plane/web/static/topology.js` 实现 scenario run/verify/restore 三组端点、并发运行冲突处理，以及 US2 独立验收所需的当前拓扑 API 与回放来源视图
- [x] T059 [P] [US2] 在 `apps/control_plane/cli.py` 实现 `scenario run|verify|restore` 和 `incidents list|explain --json`，全部调用正式 API
- [x] T060 [US2] 在 `tests/e2e/test_us2_replay.py` 验证 8 页面回放来源徽标、由正式 API 渲染的完整当前拓扑、OOM 诊断和剧本答案不会出现在诊断响应

**检查点**：US1 与 US2 组成首个可演示 MVP；无外部环境也能验证完整诊断链路。

---

## Phase 5：用户故事 3 - 准确且可追溯的资源拓扑（P1）

**目标**：稳定身份、待解析队列与时态关系正确处理同名资源、重建、重启和历史查询。

**独立测试**：两台 VM 同名容器不合并；重建关闭旧关系；故障时刻拓扑仍指向旧实例。

### 先行测试

- [x] T061 [P] [US3] 在 `tests/contract/test_inventory_api.py` 编写资源分页和 `topology?at=` 契约测试
- [x] T062 [P] [US3] 在 `tests/unit/inventory/test_identity_resolution.py` 编写唯一键、显式 alias、名称/IP 不合并和模糊匹配待确认测试
- [x] T063 [P] [US3] 在 `tests/integration/test_identity_lifecycle.py` 编写两 VM 同名容器、容器重建、服务重启和历史任务不串联测试

### 实现

- [x] T064 [US3] 在 `apps/control_plane/inventory/service.py` 实现资源 upsert、完整外部 ID 去重、显式 alias 确认与软删除
- [x] T065 [US3] 在 `apps/control_plane/inventory/relationships.py` 实现容器消失/重建、VM 重注册/迁移时关闭旧关系并建立新关系
- [x] T066 [US3] 在 `apps/control_plane/inventory/unresolved.py` 实现 unresolved 队列、待确认建议和身份补齐后的事件重算
- [x] T067 [P] [US3] 在 `agents/vm_probe/runtime.py` 实现 Docker 完整容器 ID、重启代次和服务实例身份的发现对象转换
- [x] T068 [P] [US3] 在 `apps/control_plane/api/resources.py` 实现 `GET /resources` 的 kind/provenance 过滤与稳定游标分页
- [x] T069 [US3] 在 `apps/control_plane/api/topology.py` 将 US2 当前拓扑扩展为指定时间查询，增加 root/depth 限制及截断标识
- [x] T070 [US3] 在 `apps/control_plane/web/templates/topology/index.html` 和 `apps/control_plane/web/static/topology.js` 将 US2 当前拓扑视图扩展为 Cytoscape 时态切换、历史关系和来源样式
- [x] T071 [US3] 在 `tests/e2e/test_us3_topology.py` 验证同名节点、重建前后及历史时刻切换

**检查点**：历史关联可信，US1/US2 诊断可以稳定引用故障时刻拓扑。

---

## Phase 6：用户故事 4 - 真实工作负载与采集状态（P2）

**目标**：接入真实 Docker、指标、Agent 任务与 ZSvirt 能力，断采时诚实显示未知或陈旧。

**独立测试**：正常负载运行 30 分钟形成基线；停止探针 46 秒后状态变 stale、资源变 unknown，恢复后补传。

### 先行测试

- [x] T072 [P] [US4] 在 `tests/contract/test_provider_contracts.py` 为 ReplayProvider、DockerRuntimeProvider 和 ZSvirtProvider 建立同一能力/发现/事件/指标/健康契约套件
- [x] T073 [P] [US4] 在 `tests/contract/test_probe_status_task_api.py` 编写探针注册/心跳、status、tasks 和 `/api/v1/workloads` 分页、过滤、限制、实时指标、共享延迟基线/Agent 超时、来源及 stale/gap 响应契约测试
- [x] T074 [P] [US4] 在 `tests/integration/test_probe_staleness.py` 编写三次 15 秒心跳后 stale、unknown、gap、补传和 dropped_count 测试
- [x] T075 [P] [US4] 在 `tests/integration/test_real_task_pipeline.py` 编写任务开始先写入、模型/工具分段、实例关联、R2/R4 共用 30 分钟基线及 Agent 超时等于基线 P95 三倍的测试

### 实现

- [x] T076 [P] [US4] 在 `agents/vm_probe/config.py` 和 `agents/vm_probe/identity.py` 实现安装 ID 与 VM ID 分离、短期注册令牌绑定及可轮换探针身份
- [x] T077 [P] [US4] 在 `agents/vm_probe/docker_client.py` 仅调用受限代理允许的 inspect/events/stats 接口，禁止 create/exec/delete
- [x] T078 [US4] 在 `agents/vm_probe/discovery.py` 和 `agents/vm_probe/events.py` 实现容器发现、Docker 事件断线补拉、inspect 对账和不可恢复 gap
- [x] T079 [P] [US4] 在 `agents/vm_probe/metrics.py` 暴露容器 CPU、内存、quota、throttling 指标并保持 pull/remote-write 两种路径标签一致
- [x] T080 [P] [US4] 在 `agents/vm_probe/spool.py` 实现 100 MiB 或 10 分钟先到为准的有界缓存、优先级丢弃、指数退避、`dropped_count/gap/queue_lag`
- [x] T081 [US4] 在 `apps/control_plane/api/probes.py` 实现短期令牌注册、范围凭证签发和心跳端点，越权 VM/project 写入返回 403
- [x] T082 [P] [US4] 在 T010 环境风险记录完成后，于 `deploy/prometheus/prometheus.yml`、`deploy/prometheus/agent.yml`、`deploy/node_exporter/compose.yaml` 和 `deploy/node_exporter/web-config.yml` 实现 node_exporter TLS pull 与仅出站网络的认证加密 remote-write 降级，验证受信 CA、主机名和证书有效期并保持标签一致
- [x] T083 [P] [US4] 在 `apps/control_plane/integrations/prometheus.py` 和 `apps/control_plane/diagnosis/baseline.py` 实现指标/告警查询适配、局部失败隔离、证据引用、gap 返回及 R2/R4/Agent 共用的 15 分钟 P95 基线服务，禁止规则直接写 PromQL 或各消费者复制基线算法
- [x] T084 [US4] 在 `apps/control_plane/integrations/status_service.py` 实现连接状态、最后成功、延迟、错误、权限、能力、未解析数、积压、丢弃和后端可用性汇总
- [x] T085 [US4] 在 T084 完成后，于 `apps/control_plane/api/status.py` 和 `apps/control_plane/web/templates/status/index.html` 实现 `/api/v1/status` 与状态页，未配置 ZSvirt 显示 `not_configured`
- [x] T086 [P] [US4] 在 `apps/control_plane/tasks/service.py` 和 `apps/control_plane/api/tasks.py` 实现 TaskRun 查询、状态来源和分段耗时 API
- [x] T087 [P] [US4] 在 `apps/control_plane/workloads/service.py` 和 `apps/control_plane/api/workloads.py` 按 OpenAPI 实现容器状态、限制、实时指标、T083 共享基线及 Agent 超时、来源、stale/gap 与 Grafana 深链 API
- [x] T088 [US4] 在 T086/T087 完成后，于 `apps/control_plane/web/templates/tasks/index.html` 和 `apps/control_plane/web/templates/workloads/index.html` 实现任务与工作负载页面
- [x] T089 [P] [US4] 在通过 T008 许可证准入后，于 `workloads/demo_agent/model_server/manifest.yaml`、`workloads/demo_agent/model_server/server.env` 和 `workloads/demo_agent/model_server/README.md` 固定许可允许的 CPU 小模型、llama.cpp 版本、哈希和离线启动配置
- [x] T090 [US4] 在 `workloads/demo_agent/agent.py`、`workloads/demo_agent/tool.py` 和 `workloads/demo_agent/events.py` 实现“模型调用→工具调用→汇总”及任务开始/结束、模型、工具事件，模型调用超时必须通过 T087 正式 workloads API 使用 T083 共享基线的 P95 三倍值
- [x] T091 [P] [US4] 在 `workloads/demo_agent/load_generator.py` 实现默认每 5 秒一个任务、客户端成功/失败/超时观测和可暂停速率控制
- [x] T092 [P] [US4] 在 `deploy/otel/collector.yaml` 和 `workloads/demo_agent/tracing.py` 实现标准档三段 Span，轻量档不依赖 Collector
- [x] T093 [US4] 在 T010 环境风险记录完成后，于 `integrations/zsvirt/provider.py` 按已确认字段和 TLS 端点实现只读 capabilities、增量 discover/read_events/read_metrics/health，批次与游标同事务且接口失败不删除资源
- [x] T094 [US4] 在 `tests/e2e/test_us4_real_status.py` 验证真实任务、指标更新、平台能力、断采 stale/unknown 和补传

**检查点**：真实容器、指标、任务与平台身份进入统一模型；可开始运营与真实故障实验。

---

## Phase 7：用户故事 5 - 聚合、处置并恢复事件单（P2）

**目标**：建立单一事件单事实来源、确认/静默/恢复和可量化降噪。

**独立测试**：重复告警只更新一个事件单；静默不隐藏事实；全部告警恢复并稳定 60 秒后才恢复。

### 先行测试

- [x] T095 [P] [US5] 在 `tests/contract/test_incident_operations_api.py` 编写 incidents 列表、acknowledge、silences 与降噪计数契约测试
- [x] T096 [P] [US5] 在 `tests/unit/incidents/test_fingerprint_state.py` 编写指纹、派生告警、P1/P2/P3、确认/恢复/重开状态机测试
- [x] T097 [P] [US5] 在 `tests/unit/incidents/test_silence.py` 编写 matcher、起止时间、只抑制通知和到期测试
- [x] T098 [P] [US5] 在 `tests/integration/test_alert_incident_flow.py` 编写 Prometheus/事件告警聚合、采集失联不恢复和 60 秒稳定窗口测试

### 实现

- [x] T099 [P] [US5] 在 `apps/control_plane/incidents/prometheus_poller.py` 每 15 秒读取告警状态并幂等写入 firing/resolved Alert
- [x] T100 [US5] 在 `apps/control_plane/incidents/service.py` 实现项目+规则+稳定故障对象指纹、重复计数、派生折叠、严重度与重开
- [x] T101 [US5] 在 `apps/control_plane/incidents/recovery.py` 实现全部告警 resolved 且持续 60 秒的恢复门禁，stale/unavailable 不得视为恢复
- [x] T102 [P] [US5] 在 `apps/control_plane/incidents/silence_service.py` 和 `apps/control_plane/api/silences.py` 实现 Silence 校验与通知抑制
- [x] T103 [US5] 在 `apps/control_plane/api/incidents.py` 补全列表过滤、确认操作、告警折叠和原始 Alert/Incident/通知三项计数
- [x] T104 [P] [US5] 在 `apps/control_plane/integrations/notification.py` 实现可选 NotificationSink，失败不得反向改变 Incident，静默命中不得发送
- [x] T105 [P] [US5] 在 `apps/control_plane/web/templates/incidents/index.html` 和 `apps/control_plane/web/templates/overview/index.html` 实现事件单运营与降噪视图
- [x] T106 [US5] 在 `tests/e2e/test_us5_incident_operations.py` 验证确认、静默、事实继续更新、恢复和重开流程

**检查点**：事件单成为唯一运营事实，通知保持单向可选输出。

---

## Phase 8：用户故事 6 - 安全复现和区分故障（P2）

**目标**：在授权 lab 中完成 A1/B/C/D1 的注入、诊断、验证与恢复，并实现 R2/R3/R4。

**独立测试**：四个必需场景各连续 5 次候选一致；B 与 D1 不混淆；无拓扑关系错误不合并。

### 先行测试

- [x] T107 [P] [US6] 在 `tests/contract/test_lab_scenario_security.py` 编写 prod 拒绝、测试标签、目标范围、超时和 action 允许列表测试
- [x] T108 [P] [US6] 在 `tests/unit/diagnosis/test_r2_cpu_quota.py` 编写 quota+throttling+延迟正例及无 throttling 反例
- [x] T109 [P] [US6] 在 `tests/unit/diagnosis/test_r3_tool_failure.py` 编写同 task 工具 deadline/503 正例和基础设施正常反证
- [x] T110 [P] [US6] 在 `tests/unit/diagnosis/test_r4_resource_pressure.py` 编写邻居争用、宿主机压力、缺平台证据降级和 VM 未饱和反例
- [x] T111 [P] [US6] 在 `tests/integration/test_scenario_restore.py` 编写失败/超时自动恢复、原配置恢复和 restore_failed 可见性测试
- [x] T112 [P] [US6] 在 `tests/scenario/test_required_faults.py` 编写 A1/B/C/D1 各 5 次一致、任务互证和检测耗时断言
- [x] T113 [P] [US6] 在 `tests/scenario/test_fault_counterexamples.py` 编写无关系同时错误不合并、137 非 OOM、高 GPU 使用率不归因，以及带可信映射和直接 CUDA OOM 证据的 GPU 正向回放测试

### 实现

- [x] T114 [US6] 在 `apps/control_plane/scenarios/authorization.py` 强制 `MODE=lab`、测试标签、授权项目、最长 900 秒和结构化 action 白名单
- [x] T115 [US6] 在 `apps/control_plane/scenarios/executor.py` 实现注入前快照、允许操作执行、deadline 自动恢复、显式恢复和审计事件
- [x] T116 [P] [US6] 在 `rules/R2-cpu-quota.yaml` 与 `apps/control_plane/diagnosis/evaluators/r2_cpu_quota.py` 使用 T083 共享基线服务实现 1 分钟 P95>2 倍且绝对增加>500ms、样本<30 输出基线不足，并展示窗口、样本数和基线值
- [x] T117 [P] [US6] 在 `rules/R3-tool-failure.yaml` 与 `apps/control_plane/diagnosis/evaluators/r3_tool_failure.py` 实现 task/tool 证据、deadline/HTTP 状态与基础设施反证
- [x] T118 [P] [US6] 在 `rules/R4-resource-pressure.yaml` 与 `apps/control_plane/diagnosis/evaluators/r4_resource_pressure.py` 使用 T083 同一基线服务实现 R4a 邻居争用及 R4b guest steal+ZSvirt 证据，缺平台证据时最高 medium
- [x] T119 [P] [US6] 在 `scenarios/A1-container-oom.yaml` 和 `workloads/demo_agent/faults/memory.py` 实现限时 OOM、恢复与新实例不串旧任务
- [x] T120 [P] [US6] 在 `scenarios/B-cpu-quota.yaml` 实现 quota 降低、throttling 验证、配额恢复和延迟回落断言
- [x] T121 [P] [US6] 在 `scenarios/C-tool-failure.yaml` 和 `workloads/demo_agent/faults/tool_behavior.py` 实现 deadline/503、恢复与“不归因基础设施”断言
- [x] T122 [P] [US6] 在 `scenarios/D1-neighbor-contention.yaml` 和 `workloads/demo_agent/faults/cpu_load.py` 实现带测试标签、最长 3 分钟的同 VM CPU 负载及恢复
- [x] T123 [P] [US6] 在 `fixtures/story-host-pressure.json`、`fixtures/story-gpu-cuda-oom.json` 和 `scenarios/optional-platform-pressure.yaml` 实现 R4b 必需回放、带可信设备映射/直接 CUDA OOM 证据/`replayed` 来源的 GPU 正向回放，以及能力门控 D2/D3，系统本身不得修改 ZSvirt 配置
- [x] T124 [US6] 在 `apps/control_plane/web/templates/lab/index.html` 和 `apps/control_plane/web/static/lab.js` 实现场景运行/状态/验证/恢复控件及生产禁用状态
- [x] T125 [US6] 在 `tests/e2e/test_us6_fault_comparison.py` 验证 A1 闭环、B/R2 与 D1/R4a 对照、C/R3 和全部恢复路径

**检查点**：四类真实故障闭环完成；平台能力不足时 R4b 仍可诚实回放。

---

## Phase 9：用户故事 7 - 干净环境安全复现交付物（P3）

**目标**：完成安全、安装、卸载、许可证、开销和环境声明，使第三方可独立复现。

**独立测试**：在干净 CPU-only Linux 环境仅按 README 完成安装、正常任务、真实故障、恢复和卸载；敏感原文泄露与越权写入均为 0。

### 先行测试

- [x] T126 [P] [US7] 在 `tests/security/test_sensitive_data_leakage.py` 用合成 API key、Authorization、Cookie、手机号、邮箱、提示词和工具参数扫描全部存储与导出
- [x] T127 [P] [US7] 在 `tests/security/test_authorization_boundaries.py` 和 `tests/security/test_transport_security.py` 编写跨 VM/project 写入、跨主机 HTTP、不可信 CA、主机名不匹配、过期证书、伪造转发头、Docker create/exec/delete 和 prod lab 操作拒绝测试
- [x] T128 [P] [US7] 在 `tests/integration/test_install_uninstall.py` 编写安装、凭证撤销、配置删除、默认保留数据及独立删除数据测试
- [x] T129 [P] [US7] 在 `tests/e2e/test_clean_environment.py` 编写 CPU-only 干净环境从启动到真实 A1 恢复的验证脚本

### 实现

- [x] T130 [P] [US7] 在 `deploy/docker-socket-proxy/compose.yaml` 配置仅允许 inspect/events/stats 的 Docker 受限代理并保护入口
- [x] T131 [P] [US7] 在 `deploy/profiles/{prod,lab,lightweight,standard}.env` 明确生产/实验与探针分档，prod 不启动负载发生器和写操作通道
- [x] T132 [P] [US7] 在 `deploy/install.sh` 实现依赖检查、配置安装、短期令牌注册、服务启动和可重复执行
- [x] T133 [P] [US7] 在 `deploy/uninstall.sh` 实现停止服务、撤销凭证、删除采集配置并默认保留数据，将数据删除放入独立 `deploy/purge-data.sh`
- [x] T134 [P] [US7] 在 `deploy/models/manifest.yaml` 记录通过 T008 allowlist 的开放模型版本、哈希、下载源、许可证、再分发结论与离线校验流程
- [x] T135 [US7] 在 `README.md` 完成安装/卸载、四种部署模式、实际 ZSvirt 接口/字段/权限/轮询、缺失能力、安全边界和 WSL 非 ZSvirt 声明
- [x] T136 [P] [US7] 在 `docs/security.md`、`docs/permissions.md` 和 `THIRD_PARTY_NOTICES.md` 完成脱敏、端口、跨主机 TLS 信任边界、证书签发/挂载/轮换/撤销、身份、依赖、镜像和许可证清单
- [x] T137 [P] [US7] 在 `deploy/measure-probe-overhead.sh` 和 `docs/probe-overhead.md` 记录两档 CPU、RSS、磁盘、出站流量及业务延迟影响，未实测前保留“目标而非事实”标识
- [x] T138 [US7] 在 `docs/reproduction-report.md` 记录干净 Linux 验证、环境指纹、所有命令与结果，并把 WSL、回放和 ZSvirt 结果分栏

**检查点**：交付物可以被第三方安全复现，所有能力声明都有环境和证据来源。

---

## Phase 10：打磨与跨故事质量门禁

**目标**：完成全量回归、性能实测、响应式视觉验证和最终交付审查。

- [x] T139 [P] 在 `tests/integration/test_restart_disconnect.py` 验证控制面重启不丢已提交事件、短时断网补传、证书轮换后已提交事件零丢失、spool 满载丢弃可见及单后端失败隔离
- [x] T140 [P] 在 `tests/performance/replay_scale.py` 与 `docs/capacity-report.md` 测试 10/50/100 台等价回放的样本率、事件率、队列积压和诊断延迟，声明其不替代真实网络/探针规模测试
- [x] T141 [P] 在 `tests/performance/detection_latency.py` 与 `docs/detection-latency.md` 记录事件型和持续阈值型的发生到检测/接收到检测 P50、P95 及分段耗时
- [x] T142 [P] 在 `apps/control_plane/retention.py` 和 `deploy/retention.env` 实现指标 7 天、Span 3 天、事件/诊断 30 天的可配置初值及证据过期提示
- [x] T143 [P] 在 `tests/e2e/test_responsive_pages.py` 验证 8 页面桌面/移动视口无重叠、动态内容不引起布局跳动，并保存 `docs/screenshots/iter-*.png`
- [x] T144 在 `tests/contract/`、`tests/unit/`、`tests/integration/`、`tests/scenario/` 和 `tests/e2e/` 运行全量套件并修复跨故事回归，将命令、环境和结果写入 `docs/test-report.md`，禁止通过放宽断言掩盖失败
- [x] T145 [P] 在 `LICENSE`、`THIRD_PARTY_NOTICES.md` 和 `docs/dependency-audit.md` 复核 T008 的项目许可证与 allowlist，完成依赖/镜像/模型版本哈希、再分发结论及兼容性终审
- [x] T146 在 `specs/001-zsvirt-observability-mvp/quickstart.md` 从头执行全部适用步骤并记录每条命令、实际输出、跳过原因和环境来源
- [x] T147 [P] 在 `docs/demo-script.md` 和 `docs/screenshots/` 整理 3-5 分钟演示：状态、拓扑、正常任务、故障、诊断、静默、恢复、降噪和回放标签
- [x] T148 在 `docs/release-evidence.md` 对照 FR-001 至 FR-038、SC-001 至 SC-012 和项目宪章完成最终证据索引，删除未实测性能事实并披露失败/不可用检查

**最终检查点**：规格、宪章、quickstart 和发布证据全部闭环后方可提交或公开演示。

---

## 依赖与执行顺序

### 阶段依赖

```text
Phase 1 Setup
  -> Phase 2 Foundation
      -> US1 核心诊断
          -> US2 回放闭环
              -> US3 历史拓扑
                  -> US4 真实采集与任务
                      -> US6 真实故障实验
          -> US5 事件单运营
      US1 + US2 + US3 + US4 + US5 + US6
          -> US7 可复现交付
              -> Phase 10 全量门禁
```

### 用户故事依赖

- **US1（P1）**：仅依赖 Phase 2；可通过直接事件写入独立验收。
- **US2（P1）**：依赖 US1 的诊断管线；增加 ReplayProvider、剧本执行/验证及独立验收所需的当前拓扑 API/页面。
- **US3（P1）**：依赖 US2 的当前拓扑 API/页面外壳，在其上增加身份生命周期和历史查询；可用同名与重建场景独立验收。
- **US4（P2）**：依赖 US3 的身份/关系服务；任务与状态功能不依赖 US5/US6。
- **US5（P2）**：依赖 US1 的最小 Alert/Incident；可与 US3/US4 并行扩展运营状态。
- **US6（P2）**：依赖 US1、US2、US4、US5；需要规则、场景、真实信号和事件单生命周期共同就绪。
- **US7（P3）**：依赖计划纳入交付的全部故事；可提前并行编写安装、安全和许可证材料，最终验收必须最后执行。

### 故事内部顺序

1. 先编写契约、单元、集成或场景测试并确认失败。
2. 数据模型已在 Phase 2 完成；故事内先实现领域服务，再实现 API/CLI/UI。
3. 实现后依次运行单元、契约、集成和 E2E 测试。
4. 达到故事检查点后再开始依赖该故事的阶段。

## 并行执行机会

- Setup：T003-T005、T007/T009 可在 T001 后并行；T008 在 T002 后先完成许可证准入。
- Foundation：T011-T013 可并行；T015-T022 修改不同模型文件可并行；T024-T026、T028-T030、T032-T033 可并行。
- US1：T035-T037 先并行写失败测试；T038/T039/T041/T042 可并行实现。
- US2：T050-T052 与 T056/T057 可分别并行；T058 依赖 T053-T055，T060 依赖 T058/T059。
- US3：T061-T063、T067/T068 可并行；T069/T070 依次扩展 US2 的当前拓扑 API 和页面。
- US4：T072-T075 先并行；T076/T077、T079/T080、T082/T083、T086/T087/T089、T091/T092 可按文件并行；T085 在 T084 后执行，T088 在 T086/T087 后执行。
- US5：T095-T098 先并行；T099、T102、T104、T105 可并行。
- US6：T107-T113 先并行；R2/R3/R4 和 A1/B/C/D1/R4b 文件组 T116-T123 可并行。
- US7：T126-T129 先并行；T130-T134、T136-T137 可并行。
- Final：T139-T143、T145、T147 可在核心实现冻结后并行，T144/T146/T148 串行收口。

## 各故事并行示例

```text
US1: T035 + T036 + T037；随后 T038 + T039 + T041 + T042
US2: T050 + T051 + T052；随后 T053 + T056 + T057 + T059；T058 在 T053-T055 后，最后 T060
US3: T061 + T062 + T063；随后 T067 + T068；最后 T069 + T070
US4: T072 + T073 + T074 + T075；随后按探针、指标、页面、工作负载、ZSvirt 五条支线推进
US5: T095 + T096 + T097 + T098；随后 T099 + T102 + T104 + T105
US6: T107-T113；随后 T116-T123
US7: T126-T129；随后 T130-T134 + T136-T137
```

## 实施策略

### 最小 MVP

1. 完成 Phase 1 与 Phase 2。
2. 完成 US1，通过直接事件写入证明跨层证据诊断。
3. 停止并独立验证，不进入真实采集。
4. 加入 US2 后形成第一个稳定、可浏览器演示的回放 MVP。

### 与原设计迭代的对应

| 原迭代 | 本任务阶段 |
| --- | --- |
| 迭代 0 骨架与风险验证 | Phase 1、Phase 2、US4 的能力探测前置项 |
| 迭代 1 回放页面与 R1 | US1、US2 |
| 迭代 2 真实容器 | US3、US4 探针发现 |
| 迭代 3 真实指标 | US4 指标与状态 |
| 迭代 4 ZSvirt | US4 ZSvirtProvider |
| 迭代 5 AI 工作负载 | US4 样例工作负载与基线 |
| 迭代 6 真实 OOM | US6 A1 + US5 聚合/恢复 |
| 迭代 7 其余故障与运营 | US5、US6 R2/R3/R4 |
| 迭代 8 加固交付 | US7、Phase 10 |

### 止损规则

第 15 个工作日结束时若 US6 的 A1 闭环尚未完成，在 `docs/scope-decisions.md` 记录并立即移除
Tempo、A2、D2、Webhook 和历史时间滑块 UI；随后将 D3 改为回放并合并 B/D1 注入流程。
A1、C、至少一个真实资源瓶颈、ZSvirt 身份与告警、诊断详情、事件单聚合/静默/恢复、回放规则
引擎和可复现材料不得裁剪。

## 备注

- `[P]` 只表示文件和直接依赖允许并行，不表示可跳过本阶段门禁。
- 每条规则必须同时有正例和容易混淆的反例；测试必须先失败再实现。
- 每个完成任务或逻辑任务组均应形成小型提交；当前工作区无 Git 时先保留任务证据。
- 页面和 CLI 禁止直接读取 fixture 或数据库；回放与真实数据必须通过同一正式 API。
- 所有公开性能、开销和准确性表述必须引用具体环境实测，不得把回放或 WSL 写成 ZSvirt 结果。
