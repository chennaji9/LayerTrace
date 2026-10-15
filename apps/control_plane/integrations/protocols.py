"""提供方与诊断内部契约（specs contracts/provider-contracts.md）。

类型化 Protocol：平台专有字段只允许进入版本化 attributes，
不得直接进入诊断规则。所有时间戳为带时区 UTC。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

SourceState = str  # not_configured/healthy/degraded/stale/unavailable/permission_denied


@dataclass
class CapabilityReport:
    """提供方能力清单：可用资源类型、字段、事件、指标、映射与缺失权限。"""

    resource_kinds: list[str] = field(default_factory=list)
    fields: dict[str, list[str]] = field(default_factory=dict)
    events: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    gpu_mapping: bool = False
    pagination: str | None = None  # cursor | window | none
    missing_permissions: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceHealth:
    state: SourceState
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    error_summary: str | None = None
    missing_permissions: list[str] = field(default_factory=list)


@dataclass
class DiscoveredResource:
    kind: str
    provider: str
    provider_instance: str
    source_id: str
    display_name: str | None
    provenance: str
    attributes: dict[str, Any] = field(default_factory=dict)
    namespace: str | None = None  # 稳定外部身份命名空间
    external_id: str | None = None


@dataclass
class DiscoveredRelationship:
    from_source_id: str
    type: str
    to_source_id: str
    valid_from: datetime
    valid_to: datetime | None
    source: str
    provenance: str


@dataclass
class DiscoveryBatch:
    resources: list[DiscoveredResource] = field(default_factory=list)
    relationships: list[DiscoveredRelationship] = field(default_factory=list)
    next_cursor: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass
class StandardEvent:
    """符合 events.schema.json 的标准事件（提供方侧形态）。"""

    event_id: str
    event_type: str
    namespace: str
    external_id: str
    occurred_at: datetime
    observed_at: datetime
    source: str
    provenance: str
    severity: str = "info"
    attributes: dict[str, Any] = field(default_factory=dict)
    correlation: dict[str, Any] | None = None
    raw_ref: str | None = None
    scenario_name: str | None = None
    scenario_offset: str | None = None


@dataclass
class EventBatchOut:
    events: list[StandardEvent] = field(default_factory=list)
    next_cursor: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass
class MetricSeries:
    resource_source_id: str
    metric: str
    labels: dict[str, str] = field(default_factory=dict)
    points: list[tuple[datetime, float]] = field(default_factory=list)


@dataclass
class MetricEvidenceBatch:
    series: list[MetricSeries] = field(default_factory=list)
    query_range: tuple[datetime, datetime] | None = None
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    capability_missing: list[str] = field(default_factory=list)


@dataclass
class EvidenceSummary:
    kind: str  # metric | event | snapshot | log
    resource_id: str | None
    range_start: datetime
    range_end: datetime
    source_ref: str
    provenance: str
    summary: dict[str, Any] = field(default_factory=dict)
    query_parameters: dict[str, Any] = field(default_factory=dict)
    expires_at: datetime | None = None
    gap: bool = False


@dataclass
class EvidenceBatch:
    items: list[EvidenceSummary] = field(default_factory=list)
    failed_backends: dict[str, str] = field(default_factory=dict)  # 后端 -> 错误（按信号隔离）


@dataclass
class RuntimeInstance:
    kind: str
    provider: str
    provider_instance: str
    source_id: str  # 完整容器 ID
    display_name: str | None
    provenance: str
    attributes: dict[str, Any] = field(default_factory=dict)
    restart_count: int = 0  # 重启代次
    image_digest: str | None = None
    limits: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeDiscoveryBatch:
    instances: list[RuntimeInstance] = field(default_factory=list)
    vm_namespace: str | None = None
    vm_external_id: str | None = None


@dataclass
class SnapshotOut:
    resource_source_id: str
    captured_at: datetime
    limits: dict[str, Any] = field(default_factory=dict)
    image_digest: str | None = None
    config_hash: str = ""
    provenance: str = "observed"


@dataclass
class ResourceSnapshotBatch:
    snapshots: list[SnapshotOut] = field(default_factory=list)


@runtime_checkable
class InfrastructureProvider(Protocol):
    """平台提供方（ZSvirt / Replay 共用契约）。"""

    def capabilities(self) -> CapabilityReport: ...

    async def discover(self, cursor: str | None = None) -> DiscoveryBatch: ...

    async def read_events(self, cursor: str | None = None) -> EventBatchOut: ...

    async def read_metrics(
        self, resource_ids: list[str], time_range: tuple[datetime, datetime]
    ) -> MetricEvidenceBatch: ...

    def health(self) -> SourceHealth: ...


@runtime_checkable
class RuntimeProvider(Protocol):
    """容器运行时提供方（Docker 探针侧）。"""

    async def discover_instances(self) -> RuntimeDiscoveryBatch: ...

    async def read_events(self, cursor: str | None = None) -> EventBatchOut: ...

    async def read_snapshots(self, resource_ids: list[str]) -> ResourceSnapshotBatch: ...

    def health(self) -> SourceHealth: ...


@runtime_checkable
class EvidenceQuery(Protocol):
    """证据查询适配层：诊断规则不得包含 PromQL 或后端专用查询。"""

    async def fetch(
        self,
        resource_ids: list[str],
        time_range: tuple[datetime, datetime],
        signal_kinds: list[str],
    ) -> EvidenceBatch: ...


@dataclass
class CandidateEvaluation:
    """单个规则的评估结果。"""

    rule_id: str
    rule_version: str
    title: str
    support_level: str  # high/medium/low
    score: float | None = None
    supporting: list[str] = field(default_factory=list)  # Evidence ID
    contradicting: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)
    applicable: bool = True  # False = 症状不匹配，不进入候选
    additional_candidates: list[CandidateEvaluation] = field(default_factory=list)


@dataclass
class DiagnosticContext:
    """诊断上下文：症状资源、故障时有效拓扑、时间窗与证据。"""

    symptom_resource_id: str | None
    incident_id: str
    symptom_time: datetime
    window: tuple[datetime, datetime]
    events: list[dict[str, Any]] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    snapshots: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[EvidenceSummary] = field(default_factory=list)
    topology_nodes: list[dict[str, Any]] = field(default_factory=list)
    topology_edges: list[dict[str, Any]] = field(default_factory=list)
    time_confidence: str = "trusted"
    data_gaps: list[tuple[datetime, datetime]] = field(default_factory=list)
    context_truncated: bool = False
    baseline: dict[str, Any] = field(default_factory=dict)  # 共享延迟基线（R2/R4/Agent）


@runtime_checkable
class DiagnosticRule(Protocol):
    """诊断规则 evaluator：绑定精确版本的规则元数据。"""

    rule_id: str
    version: str

    def evaluate(self, context: DiagnosticContext) -> CandidateEvaluation: ...


@dataclass
class IncidentTransition:
    incident_id: str
    fingerprint: str
    from_state: str
    to_state: str
    severity: str
    occurred_at: datetime
    silenced: bool = False


@dataclass
class DeliveryResult:
    delivered: bool
    error: str | None = None


@runtime_checkable
class NotificationSink(Protocol):
    """通知输出端：只消费事件单状态变化，失败不得反向改变事实。"""

    async def send(self, transition: IncidentTransition) -> DeliveryResult: ...
