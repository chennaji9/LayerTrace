"""US1 契约测试：事件批量写入、事件单详情与诊断修订响应。"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.contract]


def test_ingestion_result_shape() -> None:
    """IngestionResult 必须包含四个计数字段（openapi）。"""
    from packages.contracts.schemas import IngestionResult

    result = IngestionResult(accepted=1, duplicate=2, unresolved=0, rejected=0)
    body = result.model_dump()
    for key in ("accepted", "duplicate", "unresolved", "rejected"):
        assert key in body


def test_diagnosis_required_fields() -> None:
    from packages.contracts.schemas import DiagnosisCandidate

    candidate = DiagnosisCandidate(
        rule_id="R1",
        title="容器 OOM",
        support_level="high",
        supporting=[],
        contradicting=[],
        missing=[],
        recommendations=["提高内存限制"],
        verification=["kubectl describe"],
    )
    # 证据/反证/缺失数组不可缺失字段
    for field in ("supporting", "contradicting", "missing", "recommendations", "verification"):
        assert field in candidate.model_fields


def test_incident_detail_shape() -> None:
    from packages.contracts.schemas import IncidentDetail

    fields = set(IncidentDetail.model_fields)
    assert {"alerts", "timeline", "diagnosis"} <= fields
    assert {"state", "severity", "alert_count"} <= fields


def test_candidate_support_levels() -> None:
    from packages.contracts.schemas import DiagnosisCandidate

    type_ = DiagnosisCandidate.model_fields["support_level"].annotation
    assert "high" in str(type_) and "medium" in str(type_) and "low" in str(type_)
