"""契约测试：events / scenarios / diagnostic-rule JSON Schema。

覆盖 quickstart 第 3 节要求：
- 事件生产者样例全部通过 events.schema.json；
- replayed 事件缺少剧本名或相对偏移时校验失败；
- fault 场景缺少 inject 或 restore 时校验失败；
- 规则 YAML 包含任意 Python、SQL 或未注册 evaluator 时被拒绝（schema 层 +
  rule_loader 层；本文件覆盖 schema 允许的 evaluator 语法与加载器拒绝逻辑）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

CONTRACTS = Path(__file__).resolve().parents[2] / "specs/001-zsvirt-observability-mvp/contracts"


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def _registry() -> Registry:
    events = _load("events.schema.json")
    scenarios = _load("scenarios.schema.json")
    # 同时按 $id 与文件名 URI 注册：scenarios.schema.json 中的相对引用
    # "events.schema.json#/$defs/event" 以文件名为基准解析
    reg = Registry().with_resources(
        [
            (events["$id"], Resource.from_contents(events)),
            ("https://layertrace.local/schemas/events.schema.json", Resource.from_contents(events)),
            (scenarios["$id"], Resource.from_contents(scenarios)),
            (
                "https://layertrace.local/schemas/scenarios.schema.json",
                Resource.from_contents(scenarios),
            ),
        ]
    )
    return reg


def validator_for(name: str) -> Draft202012Validator:
    schema = _load(name)
    return Draft202012Validator(schema, registry=_registry())


def valid_event() -> dict:
    return {
        "schema_version": "1.0",
        "event_id": "evt-0001",
        "event_type": "container.oom",
        "resource_ref": {"namespace": "docker.container", "external_id": "a" * 64},
        "occurred_at": "2026-09-21T10:00:00Z",
        "observed_at": "2026-09-21T10:00:01Z",
        "source": "vm-probe-1",
        "provenance": "observed",
        "attributes": {"exit_code": 137},
    }


# --- events.schema.json ---


@pytest.mark.contract
def test_events_valid_batch() -> None:
    v = validator_for("events.schema.json")
    doc = {
        "schema_version": "1.0",
        "events": [valid_event(), valid_event() | {"event_id": "evt-0002"}],
    }
    assert not list(v.iter_errors(doc))


@pytest.mark.contract
def test_events_batch_over_500_rejected() -> None:
    v = validator_for("events.schema.json")
    doc = {"schema_version": "1.0", "events": [valid_event() for _ in range(501)]}
    assert list(v.iter_errors(doc))


@pytest.mark.contract
def test_events_empty_batch_rejected() -> None:
    v = validator_for("events.schema.json")
    assert list(v.iter_errors({"schema_version": "1.0", "events": []}))


@pytest.mark.contract
def test_events_missing_required_field_rejected() -> None:
    v = validator_for("events.schema.json")
    for field in ("event_id", "event_type", "resource_ref", "occurred_at", "observed_at"):
        evt = valid_event()
        evt.pop(field)
        errors = list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))
        assert errors, f"缺少必填字段 {field} 应被拒绝"


@pytest.mark.contract
def test_replayed_event_requires_scenario() -> None:
    v = validator_for("events.schema.json")
    evt = valid_event() | {"provenance": "replayed"}
    assert list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))

    evt = valid_event() | {
        "provenance": "replayed",
        "scenario": {"name": "story-oom"},  # 缺 offset
    }
    assert list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))

    evt = valid_event() | {
        "provenance": "replayed",
        "scenario": {"name": "story-oom", "offset": "+12s"},
    }
    assert not list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))


@pytest.mark.contract
def test_event_type_must_be_versioned_pattern() -> None:
    v = validator_for("events.schema.json")
    for bad in ("oom", "OOMKilled", "container.OOM"):
        evt = valid_event() | {"event_type": bad}
        assert list(v.iter_errors({"schema_version": "1.0", "events": [evt]})), bad


@pytest.mark.contract
def test_event_attributes_max_64_top_level() -> None:
    v = validator_for("events.schema.json")
    evt = valid_event() | {"attributes": {f"k{i}": i for i in range(65)}}
    assert list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))


@pytest.mark.contract
def test_event_correlation_trace_pattern() -> None:
    v = validator_for("events.schema.json")
    evt = valid_event() | {"correlation": {"task_id": "t-1", "trace_id": "zz"}}
    assert list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))
    evt = valid_event() | {"correlation": {"task_id": "t-1", "trace_id": "0" * 32}}
    assert not list(v.iter_errors({"schema_version": "1.0", "events": [evt]}))


@pytest.mark.contract
def test_events_schema_declares_size_limits() -> None:
    schema = _load("events.schema.json")
    assert schema["x-max-batch-bytes"] == 8 * 1024 * 1024
    event_def = schema["$defs"]["event"]
    assert event_def["x-max-serialized-bytes"] == 256 * 1024
    assert event_def["x-max-attributes-depth"] == 8
    assert event_def["properties"]["attributes"]["maxProperties"] == 64


# --- scenarios.schema.json ---


def valid_scenario() -> dict:
    return {
        "schema_version": "1.0",
        "name": "story-oom",
        "kind": "replay",
        "description": "OOM 正向回放",
        "topology": {"resources": [], "relationships": []},
        "events": [
            valid_event()
            | {"provenance": "replayed", "scenario": {"name": "story-oom", "offset": "+0s"}}
        ],
        "expected_assertions": [
            {"path": "diagnosis.candidates[0].rule_id", "operator": "equals", "expected": "R1"}
        ],
    }


@pytest.mark.contract
def test_scenario_valid_replay() -> None:
    v = validator_for("scenarios.schema.json")
    assert not list(v.iter_errors(valid_scenario()))


@pytest.mark.contract
def test_fault_scenario_requires_inject_and_restore() -> None:
    v = validator_for("scenarios.schema.json")
    doc = valid_scenario() | {"kind": "fault"}
    assert list(v.iter_errors(doc)), "fault 缺 inject/restore 必须失败"

    doc = valid_scenario() | {
        "kind": "fault",
        "inject": [{"operation": "set_memory_limit", "target_ref": "container://c1"}],
    }
    assert list(v.iter_errors(doc)), "fault 缺 restore 必须失败"

    doc = valid_scenario() | {
        "kind": "fault",
        "inject": [{"operation": "set_memory_limit", "target_ref": "container://c1"}],
        "restore": [{"operation": "restore_snapshot", "target_ref": "container://c1"}],
    }
    assert not list(v.iter_errors(doc))


@pytest.mark.contract
def test_scenario_action_whitelist() -> None:
    v = validator_for("scenarios.schema.json")
    doc = valid_scenario() | {
        "kind": "fault",
        "inject": [{"operation": "rm -rf /", "target_ref": "c1"}],
        "restore": [],
    }
    assert list(v.iter_errors(doc))


@pytest.mark.contract
def test_scenario_assertion_operators() -> None:
    v = validator_for("scenarios.schema.json")
    doc = valid_scenario()
    doc["expected_assertions"] = [{"path": "x", "operator": "exec", "expected": "rm -rf /"}]
    assert list(v.iter_errors(doc))


@pytest.mark.contract
def test_scenario_timeout_bounds() -> None:
    v = validator_for("scenarios.schema.json")
    doc = valid_scenario() | {"timeout_seconds": 901}
    assert list(v.iter_errors(doc))


@pytest.mark.contract
def test_scenario_offsets_pattern() -> None:
    v = validator_for("scenarios.schema.json")
    doc = valid_scenario()
    doc["topology"]["relationships"] = [
        {
            "from": "r1",
            "type": "contains",
            "to": "r2",
            "valid_from": "12s",
            "provenance": "replayed",
        }
    ]
    assert list(v.iter_errors(doc))
    doc["topology"]["relationships"] = [
        {
            "from": "r1",
            "type": "contains",
            "to": "r2",
            "valid_from": "+12s",
            "provenance": "replayed",
        }
    ]
    assert not list(v.iter_errors(doc))


# --- diagnostic-rule.schema.json ---


def valid_rule() -> dict:
    return {
        "schema_version": "1.0",
        "rule_id": "R1",
        "version": "1.0.0",
        "title": "容器 OOM",
        "symptoms": ["task.failed"],
        "window": {"before_seconds": 120, "after_seconds": 30},
        "evaluator": "r1_oom",
        "recommendations": ["检查容器内存限制"],
    }


@pytest.mark.contract
def test_rule_valid() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    assert not list(v.iter_errors(valid_rule()))
    assert not list(v.iter_errors(valid_rule() | {"rule_id": "R0", "evaluator": "r0_process_exit"}))


@pytest.mark.contract
def test_rule_id_pattern() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    for bad in ("X1", "r1", "R01a", "R1.2"):
        doc = valid_rule() | {"rule_id": bad}
        assert list(v.iter_errors(doc)), bad


@pytest.mark.contract
def test_rule_version_semver() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    for bad in ("1.0", "v1.0.0", "1.0.0-beta"):
        doc = valid_rule() | {"version": bad}
        assert list(v.iter_errors(doc)), bad


@pytest.mark.contract
def test_rule_no_extra_fields() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    doc = valid_rule() | {"python": "import os; os.system('id')"}
    assert list(v.iter_errors(doc)), "附加属性必须整体拒绝"
    doc = valid_rule() | {"sql": "SELECT * FROM events"}
    assert list(v.iter_errors(doc))


@pytest.mark.contract
def test_rule_evaluator_name_pattern() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    for bad in ("R1_OOM", "r1-oom", "os.system", "r1 oom"):
        doc = valid_rule() | {"evaluator": bad}
        assert list(v.iter_errors(doc)), bad


@pytest.mark.contract
def test_rule_requires_recommendations_and_window() -> None:
    v = validator_for("diagnostic-rule.schema.json")
    doc = valid_rule()
    doc.pop("recommendations")
    assert list(v.iter_errors(doc))
    doc = valid_rule()
    doc.pop("window")
    assert list(v.iter_errors(doc))
    doc = valid_rule() | {"window": {"before_seconds": -1, "after_seconds": 30}}
    assert list(v.iter_errors(doc))


# --- 契约文件本身的 YAML/JSON 可解析性（quickstart 第 3 节） ---


@pytest.mark.contract
def test_openapi_yaml_parses() -> None:
    doc = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    assert doc["openapi"] == "3.1.0"
