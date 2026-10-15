"""契约测试：openapi.yaml 静态结构。

校验 17 条路径（含持久证据查询）、认证声明、loopback HTTP / 跨主机 HTTPS
服务器声明、4xx 响应与 Schema 引用完整性。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "specs/001-zsvirt-observability-mvp/contracts"

EXPECTED_PATHS = {
    "/api/v1/status",
    "/api/v1/probes:register",
    "/api/v1/probes/{probe_id}:heartbeat",
    "/api/v1/events:batch",
    "/api/v1/resources",
    "/api/v1/topology",
    "/api/v1/tasks",
    "/api/v1/workloads",
    "/api/v1/incidents",
    "/api/v1/incidents/{incident_id}",
    "/api/v1/incidents/{incident_id}:acknowledge",
    "/api/v1/incidents/{incident_id}/diagnoses",
    "/api/v1/evidence/{evidence_id}",
    "/api/v1/silences",
    "/api/v1/scenarios/{scenario_name}:run",
    "/api/v1/scenario-runs/{run_id}:verify",
    "/api/v1/scenario-runs/{run_id}:restore",
}


@pytest.fixture(scope="module")
def openapi() -> dict:
    return yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))


@pytest.mark.contract
def test_exactly_17_expected_paths(openapi: dict) -> None:
    assert set(openapi["paths"]) == EXPECTED_PATHS
    assert len(EXPECTED_PATHS) == 17


@pytest.mark.contract
def test_workloads_path_present(openapi: dict) -> None:
    assert "/api/v1/workloads" in openapi["paths"]


@pytest.mark.contract
def test_global_bearer_auth(openapi: dict) -> None:
    assert openapi["security"] == [{"bearerAuth": []}]
    schemes = openapi["components"]["securitySchemes"]
    assert schemes["bearerAuth"]["type"] == "http"
    assert schemes["bearerAuth"]["scheme"] == "bearer"
    # 探针注册使用独立的短期注册令牌
    assert "registrationToken" in schemes


@pytest.mark.contract
def test_loopback_http_and_tls_https_servers(openapi: dict) -> None:
    servers = openapi["servers"]
    assert servers[0]["url"] == "http://localhost:8000"
    assert "loopback" in servers[0]["description"]
    assert servers[1]["url"].startswith("https://")
    assert any(k in servers[1]["description"] for k in ("证书", "HTTPS", "TLS"))


@pytest.mark.contract
def test_4xx_responses_declared(openapi: dict) -> None:
    # 每条路径至少声明一个 4xx 响应（认证/权限/契约/未找到）
    for path, ops in openapi["paths"].items():
        for method, op in ops.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            codes = {str(k) for k in op.get("responses", {})}
            assert codes & {"400", "401", "403", "404", "409", "413"}, (
                f"{method.upper()} {path} 缺少 4xx 响应声明"
            )


@pytest.mark.contract
def test_error_schema_referenced(openapi: dict) -> None:
    responses = openapi["components"]["responses"]
    for name in ("Unauthorized", "BadRequest", "Forbidden", "NotFound"):
        assert name in responses
        schema = responses[name]["content"]["application/json"]["schema"]
        assert schema["$ref"] == "#/components/schemas/Error"


@pytest.mark.contract
def test_all_local_refs_resolve(openapi: dict) -> None:
    import re

    text = (CONTRACTS / "openapi.yaml").read_text(encoding="utf-8")
    schemas = openapi["components"]["schemas"]
    refs = set(re.findall(r'"#/components/schemas/([A-Za-z]+)"', text))
    refs |= set(re.findall(r"#/components/schemas/([A-Za-z]+)", text))
    missing = refs - set(schemas)
    assert not missing, f"未解析的 Schema 引用：{missing}"


@pytest.mark.contract
def test_event_batch_references_events_schema(openapi: dict) -> None:
    op = openapi["paths"]["/api/v1/events:batch"]["post"]
    ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    assert ref == "./events.schema.json"
    # 413 上限声明
    assert "413" in op["responses"]
    assert "256 KiB" in op["responses"]["413"]["description"]


@pytest.mark.contract
def test_events_batch_limit_response_202(openapi: dict) -> None:
    op = openapi["paths"]["/api/v1/events:batch"]["post"]
    assert "202" in op["responses"]
    result_ref = op["responses"]["202"]["content"]["application/json"]["schema"]["$ref"]
    assert result_ref.endswith("IngestionResult")


@pytest.mark.contract
def test_pagination_limit_bounds(openapi: dict) -> None:
    limit = openapi["components"]["parameters"]["Limit"]["schema"]
    assert limit["minimum"] == 1
    assert limit["maximum"] == 200
    assert limit["default"] == 50


@pytest.mark.contract
def test_diagnosis_required_arrays(openapi: dict) -> None:
    diagnosis = openapi["components"]["schemas"]["Diagnosis"]
    required = set(diagnosis["required"])
    for field in (
        "supporting_evidence_ids",
        "contradicting_evidence_ids",
        "missing_evidence",
        "observed_impact",
        "potential_impact",
        "rule_versions",
    ):
        assert field in required
    # 分数不得命名为概率
    candidate = openapi["components"]["schemas"]["DiagnosisCandidate"]
    assert "score" in candidate["properties"]
    assert "probability" not in candidate["properties"]
    assert "不是概率" in candidate["properties"]["score"]["description"]


@pytest.mark.contract
def test_scenario_run_states(openapi: dict) -> None:
    states = openapi["components"]["schemas"]["ScenarioRun"]["properties"]["state"]["enum"]
    assert set(states) == {
        "pending", "running", "verifying", "succeeded",
        "failed", "restoring", "restored", "restore_failed",
    }


@pytest.mark.contract
def test_topology_at_parameter(openapi: dict) -> None:
    params = openapi["paths"]["/api/v1/topology"]["get"]["parameters"]
    names = [p.get("name") for p in params]
    assert "at" in names and "root_id" in names and "max_depth" in names
    depth = next(p for p in params if p["name"] == "max_depth")["schema"]
    assert depth["minimum"] == 1 and depth["maximum"] == 8
