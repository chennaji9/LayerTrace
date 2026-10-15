# 数据模型：ZSvirt 全栈可观测诊断 MVP

## 1. 公共类型与约束

### 来源类型 `Provenance`

| 值 | 含义 | 使用约束 |
| --- | --- | --- |
| `observed` | 由真实数据源直接观测 | 必须保留数据源和观测时间 |
| `simulated` | 由受控模拟器产生 | 必须显示模拟器身份 |
| `replayed` | 由固定剧本回放 | 必须记录剧本和相对偏移 |
| `inferred` | 由规则从其他事实推断 | 必须引用触发证据和规则版本 |
| `manual` | 由授权人员配置 | 必须记录操作者和配置时间 |

所有时间使用带时区的 UTC 时间戳。所有主键使用不可复用的 UUID。`tenant_id` 和 `project_id`
由服务端根据认证主体写入，禁止采集端自行声明权限范围。扩展属性使用带 `schema_version` 的
JSON 对象，未知字段不得静默丢弃。

## 2. 核心实体

### 2.1 Resource

表示项目、宿主机、虚拟机、容器、逻辑服务、服务实例、GPU 或 vGPU。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `tenant_id` | UUID | 首版固定单租户，仍作为范围字段保留 |
| `project_id` | UUID | 必填；项目自身使用其 ID |
| `kind` | enum/string | `project/host/vm/container/service/service_instance/gpu/vgpu`，允许版本化扩展 |
| `provider` | string | 来源类别，如 `zsvirt`、`docker`、`otel`、`replay` |
| `provider_instance` | string | 区分同类提供方的不同部署 |
| `source_id` | string | 提供方内稳定且完整的身份 |
| `display_name` | string | 仅用于显示，不参与去重 |
| `attributes` | JSON | 版本化扩展属性 |
| `provenance` | Provenance | 必填 |
| `first_seen` | timestamp | 首次观测，不得晚于 `last_seen` |
| `last_seen` | timestamp | 最近成功观测 |
| `deleted_at` | timestamp/null | 仅在完整对账确认消失后设置 |

唯一约束：`(tenant_id, provider, provider_instance, kind, source_id)`。

### 2.2 ResourceAlias

把外部命名空间中的身份显式绑定到资源。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `resource_id` | UUID | 外键到 Resource |
| `namespace` | string | 如 `zsvirt.vm`、`docker.container`、`otel.service_instance` |
| `external_id` | string | 完整外部 ID，禁止截断容器 ID |
| `valid_from` | timestamp | 必填 |
| `valid_to` | timestamp/null | 空表示当前有效；必须晚于 `valid_from` |
| `provenance` | Provenance | 模糊匹配不得写为 `observed` |
| `confirmed_by` | string/null | 人工确认时记录主体 |

同一命名空间和外部 ID 在同一时间只能指向一个资源。名称和 IP 只能作为发现提示，不能创建
已确认别名。

### 2.3 Relationship

表示资源之间带方向和有效期的语义关系。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `tenant_id` / `project_id` | UUID | 与两端资源范围一致 |
| `from_id` | UUID | 外键到 Resource |
| `type` | string | 见下方允许关系 |
| `to_id` | UUID | 外键到 Resource，禁止等于 `from_id` |
| `valid_from` | timestamp | 必填 |
| `valid_to` | timestamp/null | 关闭旧关系时设置 |
| `source` | string | 具体数据源 |
| `provenance` | Provenance | 必填 |
| `confidence_note` | string/null | 人工或推断关系说明 |

首版允许关系：`host hosts vm`、`vm contains container`、`container runs service_instance`、
`service_instance instance_of service`、`service depends_on service`、`vm attached_to vgpu`、
`vgpu backed_by gpu`、`resource belongs_to project`。同一关系三元组的有效区间不得重叠。

### 2.4 Event

表示不可变的结构化事实。更新通过追加纠正事件完成，禁止覆盖原记录。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 内部主键 |
| `event_id` | string | 数据源提供的幂等键，全局唯一 |
| `tenant_id` / `project_id` | UUID | 服务端写入 |
| `resource_id` | UUID/null | 解析成功后设置；失败时保留空值 |
| `resource_ref` | object | 原始命名空间与外部 ID，必须保留 |
| `type` | string | 版本化事件类型 |
| `occurred_at` | timestamp | 事实发生时间 |
| `observed_at` | timestamp | 采集端观测时间，不得缺失 |
| `severity` | enum | `info/warning/error/critical` |
| `payload` | JSON | 经允许列表和脱敏后的属性 |
| `correlation` | JSON | 可含 `task_id`、`trace_id`，不得填造未知关联 |
| `source` | string | 具体生产者 |
| `provenance` | Provenance | 必填 |
| `raw_ref` | string/null | 原始证据引用，不保存禁止内容 |
| `resolution_state` | enum | `resolved/unresolved/rejected` |
| `scenario_name` / `scenario_offset` | nullable | `replayed` 时必填 |

校验边界：单条事件按 UTF-8 JSON 序列化后最多 256 KiB；单批最多 8 MiB 或 500 条；`payload`
顶层最多 64 个属性且嵌套深度最多 8 层。任一边界超出时整批在入队和持久化前拒绝，不截断事件。

索引：`event_id` 唯一；`(tenant_id, resource_id, occurred_at)`；
`(project_id, resolution_state, observed_at)`。

### 2.5 Evidence

保存诊断可长期引用的证据摘要与查询参数，不复制全部原始指标或日志。

字段包括 `id`、`tenant_id`、`project_id`、`kind`、`resource_id`、`range_start`、`range_end`、
`source_ref`、`summary`、`query_parameters`、`provenance`、`expires_at`、`created_at`。
原始后端数据过期后记录仍保留，并标记 `source_expired=true`。

### 2.6 TaskRun

表示一次高频 Agent 任务执行，不作为长期资源节点。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `tenant_id` / `project_id` | UUID | 服务端范围 |
| `task_id` | string | 项目内唯一，由调用方和服务共同携带 |
| `service_instance_id` | UUID | 指向故障时服务实例 |
| `trace_id` | string/null | 不作为指标标签 |
| `started_at` | timestamp | 必须在执行前写入或进入 spool |
| `ended_at` | timestamp/null | 终态时设置 |
| `status` | enum | `running/succeeded/failed/timed_out/interrupted` |
| `error_class` | string/null | 结构化错误类别，不保存敏感正文 |
| `status_provenance` | Provenance | `interrupted` 可为 `inferred` |
| `trigger_event_id` | UUID/null | 推断中断时引用实例退出事件 |
| `duration_ms` / `model_duration_ms` / `tool_duration_ms` | integer/null | 非负 |

状态转换：`running -> succeeded/failed/timed_out/interrupted`。终态不可回退；迟到的观测失败作为新
事件补强证据，不覆盖原推断来源。

### 2.7 ResourceSnapshot

表示某资源在指定时刻的限制和配置。字段包括 `id`、`resource_id`、`captured_at`、`limits`、
`image_digest`、`config_hash`、`provenance`。同一资源、时刻和配置哈希去重。

### 2.8 Alert

表示规则产生的告警事实，不等同于事件单或通知。

字段包括 `id`、`tenant_id`、`project_id`、`rule_id`、`rule_version`、`fingerprint`、
`resource_id`、`state`、`severity`、`starts_at`、`ends_at`、`source`、`source_alert_id`、
`provenance`、`parent_alert_id`。`state` 为 `pending/firing/resolved`；相同来源告警通过
`(source, source_alert_id)` 幂等。

### 2.9 Incident

聚合属于同一稳定故障对象的告警，是控制面的唯一运维状态。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `tenant_id` / `project_id` | UUID | 范围 |
| `fingerprint` | string | 项目 + 规则 + 稳定故障对象；项目内唯一 |
| `state` | enum | `open/acknowledged/resolved` |
| `severity` | enum | `P1/P2/P3` |
| `first_seen` / `last_seen` | timestamp | 时间范围 |
| `resolved_at` | timestamp/null | 进入 resolved 时设置 |
| `alert_count` | integer | 至少为 1 |
| `acknowledged_by/at` | nullable | 确认审计信息 |
| `reopen_count` | integer | 初始为 0 |

状态转换：`open -> acknowledged -> resolved`，也允许 `open -> resolved`；同一指纹再次触发时
`resolved -> open` 并增加 `reopen_count`。只有全部归属 Alert 已恢复并持续 60 秒后才可恢复。

### 2.10 Silence

字段包括 `id`、`tenant_id`、`project_id`、`matcher`、`reason`、`created_by`、`starts_at`、
`ends_at`、`created_at`。`matcher` 只能是事件单指纹或 `rule_id/resource_id/severity` 的字段组合。
静默仅影响通知决策，不改变 Alert 或 Incident 状态。

### 2.11 Diagnosis

表示某事件单在一个时间点的不可变诊断修订。

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | UUID | 主键 |
| `incident_id` | UUID | 外键到 Incident |
| `revision` | integer | 从 1 开始，同一事件单单调递增且唯一 |
| `symptom_resource_id` | UUID | 诊断入口资源 |
| `window_start/window_end` | timestamp | 实际使用的关联窗口 |
| `candidates` | JSON array | 候选、支持等级、分值、建议和验证方法 |
| `supporting_evidence_ids` | UUID array | 必填，可为空数组但不能缺失字段 |
| `contradicting_evidence_ids` | UUID array | 必填 |
| `missing_evidence` | string array | 必填 |
| `observed_impact` / `potential_impact` | JSON | 两者必须明确分开 |
| `rule_versions` | JSON object | 每个 evaluator 的精确版本 |
| `time_confidence` | enum | `trusted/degraded/unknown` |
| `context_truncated` | boolean | 图遍历超限时为真 |
| `created_at` | timestamp | 不可变 |

支持等级为 `high/medium/low`；分数仅用于同级排序，不得命名为概率或置信概率。

### 2.12 SourceStatus

表示 PostgreSQL、Prometheus、ZSvirt、探针、Tempo 等数据源健康与能力。

字段包括 `id`、`source_type`、`source_instance`、`state`、`last_success_at`、`latency_ms`、
`error_code`、`error_summary`、`missing_permissions`、`capabilities`、`unresolved_count`、
`queue_lag`、`dropped_count`、`data_gaps`、`updated_at`。状态为
`not_configured/healthy/degraded/stale/unavailable/permission_denied`。超过三个 15 秒心跳周期
无数据时至少进入 `stale`，相关资源健康变为 `unknown`。

### 2.13 IngestionCursor

记录增量提供方游标。字段包括 `provider_instance`、`stream`、`cursor`、`committed_at` 和
`batch_id`。资源/事件批次与新游标必须在同一事务提交；批次失败时游标不得前移。

### 2.14 ScenarioRun

记录受控故障或回放剧本执行。

字段包括 `id`、`scenario_name`、`mode`、`state`、`target_refs`、`started_by`、`started_at`、
`deadline_at`、`ended_at`、`original_configuration`、`verification_result`、`restore_result` 和
`provenance`。状态转换为：

```text
pending -> running -> verifying -> succeeded
                   -> restoring -> restored
任意活动状态 -> failed -> restoring -> restored/restore_failed
```

真实注入只允许 `MODE=lab`、带测试标签且位于授权项目的目标。到达 `deadline_at` 必须自动恢复。

## 3. 实体关系

```text
Project 1---* Resource
Resource 1---* ResourceAlias
Resource *---* Resource          (through Relationship with validity interval)
Resource 1---* Event
Resource 1---* ResourceSnapshot
ServiceInstance 1---* TaskRun
Alert *---1 Incident
Incident 1---* Diagnosis
Diagnosis *---* Evidence
Silence *---* Incident           (dynamic matcher, notification only)
ScenarioRun 1---* Event          (through scenario identity/provenance)
SourceStatus 1---* IngestionCursor
```

## 4. 数据完整性与保留

- 指标初始保留 7 天、Span 3 天、事件与诊断 30 天，全部可配置；任何公开容量声明以实测为准。
- `task_id`、`trace_id` 和提示词禁止作为指标标签；容器完整 ID 仅作为受控标签并配合保留期。
- 所有敏感字段在 SDK、探针或 Collector 侧先脱敏再入队；服务端再次执行允许列表校验。
- 索引至少覆盖资源唯一键、有效关系、事件幂等键、事件时间查询、事件单指纹和诊断修订唯一键。
- 删除资源采用生命周期关闭和软删除；卸载默认保留已采集数据，数据删除必须走独立显式流程。
