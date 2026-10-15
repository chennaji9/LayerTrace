"""回放剧本加载器（T054）。

- 剧本只含原始事实（拓扑、事件、指标片段）与仅测试用的期望断言；
- 相对偏移（+0s/+12s）以运行基准时间展开；
- 所有回放对象写入 `replayed` 来源、剧本名与偏移；
- Schema 校验失败 / 非法 action / 引用未解析一律拒绝。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from apps.control_plane.integrations.protocols import (
    DiscoveredRelationship,
    DiscoveredResource,
    StandardEvent,
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "specs/001-zsvirt-observability-mvp/contracts"

_OFFSET_RE = re.compile(r"^\+([0-9]+)(ms|s|m)$")


class ScenarioLoadError(Exception):
    """剧本非法（Schema 校验失败、非法 action、引用未解析、缺元数据）。"""


def expand_offset(offset: str, base: datetime) -> datetime:
    match = _OFFSET_RE.match(offset)
    if not match:
        raise ScenarioLoadError(f"非法时间偏移：{offset}")
    value, unit = int(match.group(1)), match.group(2)
    delta = timedelta(milliseconds=value) if unit == "ms" else (
        timedelta(seconds=value) if unit == "s" else timedelta(minutes=value)
    )
    return base + delta


def _registry() -> Registry:
    events = json.loads((SCHEMA_DIR / "events.schema.json").read_text(encoding="utf-8"))
    scenarios = json.loads((SCHEMA_DIR / "scenarios.schema.json").read_text(encoding="utf-8"))
    return Registry().with_resources(
        [
            (events["$id"], Resource.from_contents(events)),
            ("https://layertrace.local/schemas/events.schema.json", Resource.from_contents(events)),
            (scenarios["$id"], Resource.from_contents(scenarios)),
            ("https://layertrace.local/schemas/scenarios.schema.json", Resource.from_contents(scenarios)),
        ]
    )


@dataclass
class ExpandedScenario:
    """展开后的产品侧事实（不含期望断言）。"""

    base_time: datetime
    scenario_name: str
    resources: list[DiscoveredResource] = field(default_factory=list)
    relationships: list[DiscoveredRelationship] = field(default_factory=list)
    events: list[StandardEvent] = field(default_factory=list)
    metric_fragments: list[dict] = field(default_factory=list)

    def discovery_batch(self):
        from apps.control_plane.integrations.protocols import DiscoveryBatch

        return DiscoveryBatch(resources=self.resources, relationships=self.relationships)


@dataclass
class Scenario:
    name: str
    kind: str
    description: str
    timeout_seconds: int
    raw: dict
    required_capabilities: list[str] = field(default_factory=list)
    expected_assertions: list[dict] = field(default_factory=list)

    def expand(self, base_time: datetime) -> ExpandedScenario:
        topology = self.raw.get("topology", {})
        ref_to_source: dict[str, str] = {}
        resources: list[DiscoveredResource] = []
        for res in topology.get("resources", []):
            resources.append(
                DiscoveredResource(
                    kind=res["kind"],
                    provider=res["provider"],
                    provider_instance="replay",
                    source_id=res["source_id"],
                    display_name=res.get("display_name"),
                    provenance=res["provenance"],
                    attributes=res.get("attributes", {}),
                    namespace=_namespace_for(res["provider"], res["kind"]),
                    external_id=res["source_id"],
                )
            )
            ref_to_source[res["ref"]] = res["source_id"]

        relationships: list[DiscoveredRelationship] = []
        for rel in topology.get("relationships", []):
            if rel["from"] not in ref_to_source or rel["to"] not in ref_to_source:
                raise ScenarioLoadError(f"关系引用未解析：{rel}")
            relationships.append(
                DiscoveredRelationship(
                    from_source_id=ref_to_source[rel["from"]],
                    type=rel["type"],
                    to_source_id=ref_to_source[rel["to"]],
                    valid_from=expand_offset(rel["valid_from"], base_time),
                    valid_to=expand_offset(rel["valid_to"], base_time) if rel.get("valid_to") else None,
                    source=f"replay:{self.name}",
                    provenance=rel["provenance"],
                )
            )

        events: list[StandardEvent] = []
        for evt in self.raw.get("events", []):
            occurred = expand_offset(evt["scenario"]["offset"], base_time) if evt.get("scenario") else base_time
            observed = occurred
            events.append(
                StandardEvent(
                    event_id=evt["event_id"],
                    event_type=evt["event_type"],
                    namespace=evt["resource_ref"]["namespace"],
                    external_id=evt["resource_ref"]["external_id"],
                    occurred_at=occurred,
                    observed_at=observed,
                    source=evt["source"],
                    provenance="replayed",
                    severity=evt.get("severity", "info"),
                    attributes=dict(evt.get("attributes", {})),
                    correlation=evt.get("correlation"),
                    raw_ref=evt.get("raw_ref"),
                    scenario_name=self.name,
                    scenario_offset=evt["scenario"]["offset"] if evt.get("scenario") else "+0s",
                )
            )

        return ExpandedScenario(
            base_time=base_time,
            scenario_name=self.name,
            resources=resources,
            relationships=relationships,
            events=events,
            metric_fragments=list(self.raw.get("metric_fragments", [])),
        )


def _namespace_for(provider: str, kind: str) -> str:
    mapping = {
        "zsvirt": {"host": "zsvirt.host", "vm": "zsvirt.vm", "gpu": "zsvirt.gpu", "vgpu": "zsvirt.vgpu"},
        "docker": {"container": "docker.container"},
        "otel": {"service_instance": "otel.service_instance", "service": "otel.service"},
        "replay": {"project": "layertrace.project"},
    }
    return mapping.get(provider, {}).get(kind, f"{provider}.{kind}")


def load_scenario(path: Path) -> Scenario:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yml", ".yaml"):
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ScenarioLoadError(f"剧本必须是对象：{path.name}")

    schema = json.loads((SCHEMA_DIR / "scenarios.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, registry=_registry())
    errors = sorted(validator.iter_errors(raw), key=lambda e: e.path)
    if errors:
        detail = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        raise ScenarioLoadError(f"剧本未通过 Schema 校验（{path.name}）：{detail}")

    return Scenario(
        name=raw["name"],
        kind=raw["kind"],
        description=raw.get("description", ""),
        timeout_seconds=raw.get("timeout_seconds", 180),
        raw=raw,
        required_capabilities=list(raw.get("required_capabilities", [])),
        expected_assertions=list(raw.get("expected_assertions", [])),
    )


def load_scenario_by_name(fixtures_dir: Path, name: str) -> Scenario:
    for suffix in (".json", ".yaml", ".yml"):
        candidate = fixtures_dir / f"{name}{suffix}"
        if candidate.exists():
            return load_scenario(candidate)
    raise ScenarioLoadError(f"剧本不存在：{name}")


def as_dict(obj: Any) -> dict:  # pragma: no cover - 调试辅助
    if isinstance(obj, dict):
        return obj
    return vars(obj)
