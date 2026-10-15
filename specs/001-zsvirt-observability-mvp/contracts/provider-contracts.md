# 提供方与诊断内部契约

这些接口是 LayerTrace 内部适配器边界，不代表 ZSvirt 官方接口。实现必须返回公共领域对象，
不得让平台专有字段直接进入诊断规则；专有字段放入带版本的 `attributes`。

## InfrastructureProvider

```text
capabilities() -> CapabilityReport
discover(cursor?) -> DiscoveryBatch
read_events(cursor?) -> EventBatch
read_metrics(resource_ids, time_range) -> MetricEvidenceBatch
health() -> SourceHealth
```

- `capabilities` 返回可用资源类型、字段、事件、指标、GPU 映射、分页/游标和权限缺失。
- `discover` 返回资源、时态关系和下一游标。批次与游标必须同事务提交。
- `read_events` 返回符合 `events.schema.json` 的标准事件；无游标接口必须使用重叠窗口和去重。
- `read_metrics` 返回序列、查询范围和数据空档；不支持时返回明确 capability，不得返回空健康数据。
- `health` 返回状态、最后成功时间、延迟、错误摘要和缺失权限。

首版实现 `ZSvirtProvider` 和 `ReplayProvider`。两者必须通过同一契约测试套件。

## RuntimeProvider

```text
discover_instances() -> RuntimeDiscoveryBatch
read_events(cursor?) -> EventBatch
read_snapshots(resource_ids) -> ResourceSnapshotBatch
health() -> SourceHealth
```

Docker 实现必须使用完整容器 ID，并区分容器重建与同一容器重启代次。API 访问必须经过受限代理。

## EvidenceQuery

```text
fetch(resource_ids, time_range, signal_kinds) -> EvidenceBatch
```

返回证据摘要、稳定引用、来源、查询参数、过期时间和 gap。后端失败按信号类型隔离，禁止使整个
诊断请求失败。诊断规则不得直接包含 PromQL 或后端专用查询。

## DiagnosticRule

```text
evaluate(DiagnosticContext) -> CandidateEvaluation
```

`DiagnosticContext` 包含症状资源、故障时有效拓扑、时间窗、事件、指标证据、快照、任务状态、
时钟可信度和数据空档。输出必须包含候选、支持证据、反证、缺失证据、支持等级、建议和验证方法。
每个 evaluator 必须绑定符合 `diagnostic-rule.schema.json` 的规则元数据和精确版本。

## NotificationSink

```text
send(IncidentTransition) -> DeliveryResult
```

通知只消费控制面的事件单状态变化。静默命中时不调用；通知失败不得反向改变告警或事件单事实。
