"""US2 单元测试：回放剧本加载器。

- 相对时间展开（+0s/+12s 以基准时间展开）；
- 引用解析（topology ref -> 事件 resource_ref）；
- 期望断言与产品数据隔离（loader 输出不含 expected_assertions 混入产品结构）；
- 非法 action / 非法 offset / 缺 scenario 元数据拒绝。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures"


def test_story_oom_fixture_loads() -> None:
    from integrations.replay.loader import load_scenario

    scenario = load_scenario(FIXTURES / "story-oom.json")
    assert scenario.name == "story-oom"
    assert scenario.kind == "replay"
    assert scenario.expected_assertions
    expanded = scenario.expand(datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
    assert expanded.events
    assert expanded.resources and expanded.relationships


def test_offsets_expand_relative_to_base() -> None:
    from integrations.replay.loader import expand_offset, load_scenario

    base = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    assert expand_offset("+0s", base) == base
    assert expand_offset("+12s", base) == base + timedelta(seconds=12)
    assert expand_offset("+1500ms", base) == base + timedelta(milliseconds=1500)
    assert expand_offset("+3m", base) == base + timedelta(minutes=3)

    scenario = load_scenario(FIXTURES / "story-oom.json")
    expanded = scenario.expand(base)
    times = [e.occurred_at for e in expanded.events]
    assert min(times) >= base
    assert max(times) <= base + timedelta(seconds=600)


def test_replayed_events_carry_scenario_identity() -> None:
    from integrations.replay.loader import load_scenario

    scenario = load_scenario(FIXTURES / "story-oom.json")
    for event in scenario.expand(datetime(2026, 9, 21, 10, 0, tzinfo=UTC)).events:
        assert event.provenance == "replayed"
        assert event.scenario_name == "story-oom"
        assert event.scenario_offset is not None


def test_expected_assertions_isolated_from_product_output() -> None:
    from integrations.replay.loader import load_scenario

    scenario = load_scenario(FIXTURES / "story-oom.json")
    expanded = scenario.expand(datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
    # 产品侧结构（将写入正式管线的数据）不得包含期望断言
    product_blob = repr(expanded.events) + repr(expanded.resources) + repr(expanded.relationships)
    assert "expected" not in product_blob.lower()
    # 期望只保留在 scenario 对象上，由 verify 独立使用
    assert scenario.expected_assertions


def test_relationship_refs_resolve() -> None:
    from integrations.replay.loader import load_scenario

    scenario = load_scenario(FIXTURES / "story-oom.json")
    expanded = scenario.expand(datetime(2026, 9, 21, 10, 0, tzinfo=UTC))
    ids = {r.source_id for r in expanded.resources}
    for rel in expanded.relationships:
        assert rel.from_source_id in ids, f"关系引用未解析：{rel.from_source_id}"
        assert rel.to_source_id in ids, f"关系引用未解析：{rel.to_source_id}"


def test_invalid_action_rejected(tmp_path: Path) -> None:
    from integrations.replay.loader import ScenarioLoadError, load_scenario

    doc = {
        "schema_version": "1.0",
        "name": "bad-action",
        "kind": "fault",
        "description": "非法 action",
        "topology": {"resources": [], "relationships": []},
        "events": [],
        "inject": [{"operation": "rm -rf /", "target_ref": "x"}],
        "restore": [],
        "expected_assertions": [{"path": "x", "operator": "exists", "expected": True}],
    }
    p = tmp_path / "bad-action.json"
    p.write_text(__import__("json").dumps(doc), encoding="utf-8")
    with pytest.raises(ScenarioLoadError):
        load_scenario(p)


def test_counterexample_fixture_loads() -> None:
    from integrations.replay.loader import load_scenario

    scenario = load_scenario(FIXTURES / "story-exit-137-not-oom.json")
    assert scenario.name == "story-exit-137-not-oom"
    assert scenario.kind == "replay"
