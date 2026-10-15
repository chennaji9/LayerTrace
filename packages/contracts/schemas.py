"""共享 Pydantic 契约（OpenAPI / events.schema.json 的运行时形态）。

硬性上限与规格 FR-030 一致：
- 单条事件 UTF-8 JSON 序列化 ≤ 256 KiB；
- 单批 ≤ 8 MiB 或 500 条；
- payload 顶层属性 ≤ 64 项、嵌套深度 ≤ 8 层；
- namespace ≤ 128、external_id ≤ 512；
任一超限抛出 ContractLimitError，由 API 层对整批返回 413（不截断、不接受）。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_EVENT_BYTES = 256 * 1024
MAX_BATCH_BYTES = 8 * 1024 * 1024
MAX_BATCH_EVENTS = 500
MAX_ATTRIBUTES = 64
MAX_ATTRIBUTE_DEPTH = 8

UUID_HEX = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

Provenance = Literal["observed", "simulated", "replayed", "inferred", "manual"]
Severity = Literal["info", "warning", "error", "critical"]
SourceStateLiteral = Literal[
    "not_configured", "healthy", "degraded", "stale", "unavailable", "permission_denied"
]
TaskStatusLiteral = Literal["running", "succeeded", "failed", "timed_out", "interrupted"]
IncidentStateLiteral = Literal["open", "acknowledged", "resolved"]
IncidentSeverityLiteral = Literal["P1", "P2", "P3"]
SupportLevelLiteral = Literal["high", "medium", "low"]
TimeConfidenceLiteral = Literal["trusted", "degraded", "unknown"]
ScenarioRunStateLiteral = Literal[
    "pending",
    "running",
    "verifying",
    "succeeded",
    "failed",
    "restoring",
    "restored",
    "restore_failed",
]


class ContractLimitError(Exception):
    """载荷超过契约上限；API 层映射为 413，可定位到限制类型。"""

    def __init__(self, limit_type: str, detail: str) -> None:
        self.limit_type = limit_type
        self.detail = detail
        super().__init__(f"{limit_type}: {detail}")


def _utf8_size(obj: Any) -> int:
    return len(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _value_depth(value: Any, depth: int = 1) -> int:
    if isinstance(value, dict):
        return max((_value_depth(v, depth + 1) for v in value.values()), default=depth)
    if isinstance(value, list):
        return max((_value_depth(v, depth + 1) for v in value), default=depth)
    return depth


class ResourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: str = Field(min_length=1, max_length=128)
    external_id: str = Field(min_length=1, max_length=512)


class ScenarioRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    offset: str = Field(pattern=r"^\+[0-9]+(ms|s|m)$")


class Correlation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str | None = Field(default=None, max_length=256)
    trace_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{32}$")


class EventInput(BaseModel):
    """单条标准事件（对应 events.schema.json #/$defs/event）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    event_id: str = Field(min_length=1, max_length=256)
    event_type: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"
    )
    resource_ref: ResourceRef
    occurred_at: datetime
    observed_at: datetime
    severity: Severity = "info"
    source: str = Field(min_length=1, max_length=128)
    provenance: Provenance
    correlation: Correlation | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    raw_ref: str | None = Field(default=None, max_length=1024)
    scenario: ScenarioRef | None = None

    @field_validator("attributes")
    @classmethod
    def _check_attributes(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > MAX_ATTRIBUTES:
            raise ContractLimitError(
                "attributes_count", f"顶层属性 {len(v)} 项超过上限 {MAX_ATTRIBUTES}"
            )
        depth = _value_depth(v)
        if depth > MAX_ATTRIBUTE_DEPTH:
            raise ContractLimitError(
                "attributes_depth", f"嵌套深度 {depth} 超过上限 {MAX_ATTRIBUTE_DEPTH}"
            )
        return v

    @model_validator(mode="after")
    def _check_replayed_and_size(self) -> EventInput:
        if self.provenance == "replayed" and self.scenario is None:
            raise ValueError("replayed 事件必须携带 scenario 名称与偏移")
        size = _utf8_size(self.model_dump(mode="json"))
        if size > MAX_EVENT_BYTES:
            raise ContractLimitError(
                "event_bytes", f"事件序列化 {size} 字节超过 {MAX_EVENT_BYTES}（256 KiB）"
            )
        return self


class EventBatch(BaseModel):
    """事件批次（对应 events.schema.json 顶层）。"""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"]
    events: list[EventInput] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_batch_size(self) -> EventBatch:
        if len(self.events) > MAX_BATCH_EVENTS:
            raise ContractLimitError(
                "batch_events", f"批次 {len(self.events)} 条超过上限 {MAX_BATCH_EVENTS}"
            )
        size = _utf8_size(self.model_dump(mode="json"))
        if size > MAX_BATCH_BYTES:
            raise ContractLimitError(
                "batch_bytes", f"批次序列化 {size} 字节超过 {MAX_BATCH_BYTES}（8 MiB）"
            )
        return self


class IngestionResult(BaseModel):
    accepted: int = Field(ge=0)
    duplicate: int = Field(ge=0)
    unresolved: int = Field(ge=0)
    rejected: int = Field(ge=0)
    errors: list[ErrorOut] = Field(default_factory=list)


class ErrorOut(BaseModel):
    code: str
    message: str
    field: str | None = None


class ResourceOut(BaseModel):
    id: str
    kind: str
    provider: str
    provider_instance: str
    source_id: str
    display_name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    first_seen: datetime
    last_seen: datetime


class RelationshipOut(BaseModel):
    id: str
    from_id: str
    type: str
    to_id: str
    valid_from: datetime
    valid_to: datetime | None = None
    source: str
    provenance: Provenance


class ResourcePage(BaseModel):
    items: list[ResourceOut]
    next_cursor: str | None = None


class TopologyResponse(BaseModel):
    at: datetime
    nodes: list[ResourceOut]
    edges: list[RelationshipOut]
    truncated: bool


class TaskRunOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: str
    task_id: str
    service_instance_id: str
    trace_id: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    status: TaskStatusLiteral
    status_provenance: Provenance
    error_class: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    model_duration_ms: int | None = Field(default=None, ge=0)
    tool_duration_ms: int | None = Field(default=None, ge=0)


class TaskRunPage(BaseModel):
    items: list[TaskRunOut]
    next_cursor: str | None = None


class LatencyBaseline(BaseModel):
    state: Literal["ready", "insufficient"]
    window_seconds: int = 900
    sample_count: int = Field(ge=0)
    p95_ms: float | None = Field(default=None, ge=0)
    multiplier: int = 3
    agent_timeout_ms: int | None = Field(default=None, ge=1)


class WorkloadOut(BaseModel):
    id: str
    container_id: str
    display_name: str
    state: Literal["running", "stopped", "unknown"]
    data_state: Literal["current", "stale", "gap", "unavailable"]
    limits: dict[str, Any]
    metrics: dict[str, Any]
    latency_baseline: LatencyBaseline
    dashboard_url: str | None = None
    provenance: Provenance
    observed_at: datetime


class WorkloadPage(BaseModel):
    items: list[WorkloadOut]
    next_cursor: str | None = None


class DiagnosisCandidate(BaseModel):
    rule_id: str
    title: str
    support_level: SupportLevelLiteral
    score: float | None = Field(default=None, description="规则支持程度，不是概率")
    supporting: list[str]
    contradicting: list[str]
    missing: list[str]
    recommendations: list[str]
    verification: list[str]
    detail: dict[str, Any] = Field(default_factory=dict)


class DiagnosisOut(BaseModel):
    id: str
    incident_id: str
    revision: int = Field(ge=1)
    candidates: list[DiagnosisCandidate]
    supporting_evidence_ids: list[str]
    contradicting_evidence_ids: list[str]
    missing_evidence: list[str]
    observed_impact: dict[str, Any]
    potential_impact: dict[str, Any]
    rule_versions: dict[str, str]
    time_confidence: TimeConfidenceLiteral
    context_truncated: bool = False
    created_at: datetime


class EvidenceOut(BaseModel):
    id: str
    kind: str
    resource_id: str | None = None
    range_start: datetime
    range_end: datetime
    source_ref: str
    summary: dict[str, Any]
    query_parameters: dict[str, Any]
    provenance: Provenance
    source_expired: bool


class IncidentSummary(BaseModel):
    id: str
    fingerprint: str
    state: IncidentStateLiteral
    severity: IncidentSeverityLiteral
    first_seen: datetime
    last_seen: datetime
    alert_count: int = Field(ge=1)
    silenced_until: datetime | None = None


class IncidentPage(BaseModel):
    items: list[IncidentSummary]
    next_cursor: str | None = None
    raw_alert_count: int = Field(ge=0)
    incident_count: int = Field(ge=0)
    notification_count: int = Field(ge=0)


class IncidentDetail(IncidentSummary):
    alerts: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    diagnosis: DiagnosisOut | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class SilenceMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fingerprint: str | None = None
    rule_id: str | None = None
    resource_id: str | None = Field(default=None, pattern=UUID_HEX)
    severity: IncidentSeverityLiteral | None = None


class SilenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matcher: SilenceMatcher
    reason: str = Field(min_length=1, max_length=1000)
    created_by: str = Field(min_length=1)
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def _check(self) -> SilenceRequest:
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at 必须晚于 starts_at")
        keys = [k for k, v in self.matcher.model_dump().items() if v is not None]
        if not 1 <= len(keys) <= 3:
            raise ValueError("matcher 必须是 1-3 个字段组合")
        return self


class SilenceOut(SilenceRequest):
    id: str
    created_at: datetime


class ScenarioRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["replay", "lab"]
    target_refs: list[ResourceRef] = Field(default_factory=list)
    timeout_seconds: int = Field(default=180, ge=1, le=900)


class ScenarioRunOut(BaseModel):
    id: str
    scenario_name: str
    mode: Literal["replay", "lab"]
    state: ScenarioRunStateLiteral
    started_at: datetime
    deadline_at: datetime
    ended_at: datetime | None = None
    verification_result: dict[str, Any] | None = None
    restore_result: dict[str, Any] | None = None


class SourceStatusOut(BaseModel):
    type: str
    instance: str
    state: SourceStateLiteral
    last_success_at: datetime | None = None
    latency_ms: int | None = None
    error_summary: str | None = None
    missing_permissions: list[str] = Field(default_factory=list)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    unresolved_count: int = Field(ge=0)
    queue_lag_seconds: float | None = None
    dropped_count: int = Field(ge=0)
    data_gaps: list[dict[str, datetime]] = Field(default_factory=list)


class StatusResponse(BaseModel):
    generated_at: datetime
    mode: Literal["prod", "lab", "replay"]
    sources: list[SourceStatusOut]
    unresolved_count: int = Field(ge=0)
    diagnosis_error_count: int = Field(ge=0)


class ProbeRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str = Field(min_length=1, max_length=256)
    vm_ref: ResourceRef
    profile: Literal["lightweight", "standard"]
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ProbeRegistrationResult(BaseModel):
    probe_id: str
    vm_id: str
    credential: str
    expires_at: datetime


class ProbeHeartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observed_at: datetime
    queue_depth: int = Field(ge=0)
    dropped_count: int = Field(ge=0)
    oldest_queued_at: datetime | None = None
    clock_offset_seconds: float | None = None
    gaps: list[dict[str, datetime]] = Field(default_factory=list)
