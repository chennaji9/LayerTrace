"""US3 契约测试：资源分页与 topology?at= 历史查询。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.contract]


def test_resource_page_contract() -> None:
    from packages.contracts.schemas import ResourcePage

    fields = set(ResourcePage.model_fields)
    assert {"items", "next_cursor"} == fields


def test_resource_out_contract() -> None:
    from packages.contracts.schemas import ResourceOut

    fields = set(ResourceOut.model_fields)
    assert {"id", "kind", "provider", "provider_instance", "source_id", "provenance", "first_seen", "last_seen"} <= fields


def test_topology_at_parameter_declared() -> None:
    from pathlib import Path

    import yaml

    contracts = Path(__file__).resolve().parents[2] / "specs/001-zsvirt-observability-mvp/contracts/openapi.yaml"
    spec = yaml.safe_load(contracts.read_text(encoding="utf-8"))
    params = spec["paths"]["/api/v1/topology"]["get"]["parameters"]
    at_param = next(p for p in params if p.get("name") == "at")
    assert at_param["in"] == "query"
    assert "省略时返回当前拓扑" in at_param["description"]
