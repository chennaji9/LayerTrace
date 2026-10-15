"""US3 单元测试：身份解析规则。

- 唯一键 (tenant, provider, provider_instance, kind, source_id)；
- 显式 alias 才能合并跨源身份；
- 名称 / IP 不合并：同名资源保持独立；
- 模糊匹配只产生待确认建议（unresolved），不自动合并。
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.unit]

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")


def test_same_name_different_source_not_merged() -> None:
    """名称不是身份：同名不同 source_id 必须是两个资源；同一 source_id 才是同一资源。"""
    from apps.control_plane.inventory.identity import same_display_name_is_not_identity

    assert same_display_name_is_not_identity("web", "web", "aaa", "bbb")
    assert not same_display_name_is_not_identity("web", "web", "aaa", "aaa")


def test_fuzzy_match_produces_suggestion_only() -> None:
    """模糊匹配（名称/IP 相似）只产生待确认建议。"""
    from apps.control_plane.inventory.unresolved import build_suggestions

    candidates = build_suggestions(
        incoming={"namespace": "otel.service_instance", "external_id": "demo-agent@10.0.0.5"},
        known_resources=[
            {"id": "r1", "display_name": "demo-agent", "source_id": "si-1", "kind": "service_instance"},
            {"id": "r2", "display_name": "other", "source_id": "si-2", "kind": "service_instance"},
        ],
    )
    assert candidates, "应产生待确认建议"
    assert all(s["confirmed"] is False for s in candidates)
    assert all("demo-agent" in s["reason"] or "name" in s["reason"].lower() for s in candidates)


def test_alias_is_explicit_and_namespaced() -> None:
    from apps.control_plane.inventory.identity import alias_key

    key = alias_key("zsvirt.vm", "vm-123")
    assert key == ("zsvirt.vm", "vm-123")


def test_identity_key_components() -> None:
    from apps.control_plane.inventory.identity import identity_key

    key = identity_key(
        tenant_id=TENANT, provider="docker", provider_instance="default",
        kind="container", source_id="abc",
    )
    assert key == (TENANT, "docker", "default", "container", "abc")
