"""ReplayProvider：与 ZSvirtProvider 同一 InfrastructureProvider 契约的回放提供方。

- discover 输出回放资源与当前有效关系（供拓扑查询）；
- read_events 输出符合 events.schema.json 的标准事件（replayed 来源）；
- read_metrics 输出剧本指标片段；
- 无外部依赖：无 GPU、无 ZSvirt、无外部模型时仍可点亮完整链路。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apps.control_plane.integrations.protocols import (
    CapabilityReport,
    DiscoveryBatch,
    EventBatchOut,
    MetricEvidenceBatch,
    MetricSeries,
    SourceHealth,
)
from integrations.replay.loader import Scenario, load_scenario_by_name

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


class ReplayProvider:
    """基于固定剧本的只读基础设施提供方。"""

    def __init__(self, scenario: Scenario, base_time: datetime | None = None) -> None:
        self._scenario = scenario
        self._base_time = base_time or datetime.now(UTC)

    # -- InfrastructureProvider 契约 --

    def capabilities(self) -> CapabilityReport:
        return CapabilityReport(
            resource_kinds=sorted({r["kind"] for r in self._scenario.raw.get("topology", {}).get("resources", [])}),
            fields={"replay": ["source_id", "kind", "provider"]},
            events=sorted({e["event_type"] for e in self._scenario.raw.get("events", [])}),
            metrics=[f.get("metric", "") for f in self._scenario.raw.get("metric_fragments", [])],
            gpu_mapping=False,
            pagination="none",
            missing_permissions=[],
            notes={"mode": "replay", "scenario": self._scenario.name},
        )

    async def discover(self, cursor: str | None = None) -> DiscoveryBatch:
        expanded = self._scenario.expand(self._base_time)
        return expanded.discovery_batch()

    async def read_events(self, cursor: str | None = None) -> EventBatchOut:
        expanded = self._scenario.expand(self._base_time)
        events = sorted(expanded.events, key=lambda e: (e.occurred_at, e.event_id))
        return EventBatchOut(
            events=events,
            next_cursor=None,
            window_start=events[0].occurred_at if events else None,
            window_end=events[-1].occurred_at if events else None,
        )

    async def read_metrics(
        self, resource_ids: list[str], time_range: tuple[datetime, datetime]
    ) -> MetricEvidenceBatch:
        series: list[MetricSeries] = []
        for fragment in self._scenario.raw.get("metric_fragments", []):
            if resource_ids and fragment.get("resource_ref") not in resource_ids:
                continue
            points = []
            for point in fragment.get("points", []):
                from integrations.replay.loader import expand_offset

                at = expand_offset(point["offset"], self._base_time)
                if time_range[0] <= at <= time_range[1]:
                    points.append((at, float(point["value"])))
            if points:
                series.append(
                    MetricSeries(
                        resource_source_id=fragment["resource_ref"],
                        metric=fragment["metric"],
                        labels=fragment.get("labels", {}),
                        points=points,
                    )
                )
        return MetricEvidenceBatch(series=series, query_range=time_range)

    def health(self) -> SourceHealth:
        return SourceHealth(state="healthy", last_success_at=self._base_time, latency_ms=0)


def replay_provider_for(name: str, base_time: datetime | None = None) -> ReplayProvider:
    scenario = load_scenario_by_name(FIXTURES_DIR, name)
    return ReplayProvider(scenario, base_time)
