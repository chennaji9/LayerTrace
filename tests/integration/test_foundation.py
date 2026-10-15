"""Phase 2 门禁：认证范围、传输安全、413、脱敏、幂等、规则拒绝与 8 页面外壳。"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration]

TEST_TOKEN = "test-token-foundation-0001"


@pytest.fixture(scope="module")
def app_client(test_database_url) -> TestClient:
    """构建指向测试库的应用客户端（模块级共享，避免重复迁移）。"""
    os.environ["DATABASE_URL"] = test_database_url
    os.environ["API_TOKENS"] = TEST_TOKEN
    from apps.control_plane.config import get_settings

    get_settings.cache_clear()

    from apps.control_plane.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client

    get_settings.cache_clear()
    os.environ.pop("API_TOKENS", None)


def _auth(client: TestClient) -> dict:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


def _event(event_id: str = "evt-1", **overrides) -> dict:
    event = {
        "schema_version": "1.0",
        "event_id": event_id,
        "event_type": "container.oom",
        "resource_ref": {"namespace": "docker.container", "external_id": "f" * 64},
        "occurred_at": "2026-09-21T10:00:00Z",
        "observed_at": "2026-09-21T10:00:01Z",
        "severity": "error",
        "source": "probe-test",
        "provenance": "observed",
        "attributes": {"exit_code": 137},
    }
    event.update(overrides)
    return event


def _batch(events: list[dict]) -> dict:
    return {"schema_version": "1.0", "events": events}


# --- 认证 ---

async def test_missing_token_401(app_client: TestClient) -> None:
    res = app_client.post("/api/v1/events:batch", json=_batch([_event("auth-1")]))
    assert res.status_code == 401
    assert res.json()["detail"]["code"] == "unauthorized"


async def test_invalid_token_401(app_client: TestClient) -> None:
    res = app_client.post(
        "/api/v1/events:batch",
        json=_batch([_event("auth-2")]),
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res.status_code == 401


async def test_valid_token_202(app_client: TestClient) -> None:
    res = app_client.post(
        "/api/v1/events:batch", json=_batch([_event("auth-3")]), headers=_auth(app_client)
    )
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["accepted"] == 1


async def test_client_cannot_override_tenant(app_client: TestClient) -> None:
    # 契约模型不含 tenant_id/project_id；客户端注入的额外字段整体拒绝
    evt = _event("scope-1") | {"tenant_id": "00000000-0000-0000-0000-00000000dead"}
    res = app_client.post("/api/v1/events:batch", json=_batch([evt]), headers=_auth(app_client))
    assert res.status_code in (400, 422)


# --- 传输安全（直接驱动 ASGI 中间件） ---

def _run_middleware(client_ip: str | None, scheme: str, headers: list[tuple[bytes, bytes]]):
    """以指定客户端上下文直接调用 TransportSecurityMiddleware。"""
    from apps.control_plane.middleware.transport_security import TransportSecurityMiddleware

    captured: dict = {}

    async def inner_app(scope, receive, send):
        captured["scheme"] = scope.get("scheme")
        captured["headers"] = dict(scope.get("headers", []))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    scope = {
        "type": "http",
        "scheme": scheme,
        "http_version": "1.1",
        "method": "GET",
        "path": "/api/v1/status",
        "raw_path": b"/api/v1/status",
        "query_string": b"",
        "root_path": "",
        "client": (client_ip, 50000) if client_ip else None,
        "server": ("api", 8000),
        "headers": headers,
    }

    result: dict = {}

    async def receive():  # pragma: no cover
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        result[message["type"]] = message

    middleware = TransportSecurityMiddleware(inner_app)
    asyncio.run(middleware(scope, receive, send))
    return result, captured


def test_plaintext_off_loopback_rejected() -> None:
    result, _ = _run_middleware("192.168.10.20", "http", [])
    status = result.get("http.response.start", {}).get("status")
    assert status == 426
    body = result.get("http.response.body", {}).get("body", b"")
    assert b"transport_security" in body


def test_loopback_http_allowed() -> None:
    result, captured = _run_middleware("127.0.0.1", "http", [])
    assert result["http.response.start"]["status"] == 200
    assert captured["scheme"] == "http"


def test_explicit_compose_gateway_http_allowed(monkeypatch) -> None:
    from apps.control_plane.config import get_settings

    monkeypatch.setenv("PLAINTEXT_CLIENT_CIDRS", "172.31.255.1/32")
    get_settings.cache_clear()
    try:
        result, captured = _run_middleware("172.31.255.1", "http", [])
        assert result["http.response.start"]["status"] == 200
        assert captured["scheme"] == "http"
    finally:
        get_settings.cache_clear()


def test_https_direct_allowed() -> None:
    result, _ = _run_middleware("192.168.10.20", "https", [])
    assert result["http.response.start"]["status"] == 200


def test_untrusted_forwarded_proto_ignored() -> None:
    # 不可信客户端伪造 X-Forwarded-Proto: https 不能把明文请求洗白
    result, _ = _run_middleware(
        "203.0.113.9", "http", [(b"x-forwarded-proto", b"https")]
    )
    assert result["http.response.start"]["status"] == 426


def test_trusted_proxy_https_forward_allowed(monkeypatch) -> None:
    from apps.control_plane.config import get_settings

    monkeypatch.setenv("TRUSTED_PROXIES", "172.18.0.5/32")
    get_settings.cache_clear()
    try:
        result, captured = _run_middleware(
            "172.18.0.5", "http", [(b"x-forwarded-proto", b"https")]
        )
        assert result["http.response.start"]["status"] == 200
        assert captured["scheme"] == "https"
    finally:
        get_settings.cache_clear()


# --- 载荷上限 413 ---

async def test_event_over_65_attributes_413(app_client: TestClient) -> None:
    attrs = {f"k{i}": i for i in range(65)}
    res = app_client.post(
        "/api/v1/events:batch",
        json=_batch([_event("limit-1", attributes=attrs)]),
        headers=_auth(app_client),
    )
    assert res.status_code == 413
    assert "attributes_count" in res.text


async def test_event_nesting_over_8_levels_413(app_client: TestClient) -> None:
    nested = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": 1}}}}}}}}}
    res = app_client.post(
        "/api/v1/events:batch",
        json=_batch([_event("limit-2", attributes={"deep": nested})]),
        headers=_auth(app_client),
    )
    assert res.status_code == 413
    assert "attributes_depth" in res.text


async def test_event_over_256kib_413(app_client: TestClient) -> None:
    big = {"blob": "x" * (270 * 1024)}
    res = app_client.post(
        "/api/v1/events:batch",
        json=_batch([_event("limit-3", attributes=big)]),
        headers=_auth(app_client),
    )
    assert res.status_code == 413
    assert "event_bytes" in res.text


async def test_batch_over_500_events_413(app_client: TestClient) -> None:
    events = [_event(f"limit-batch-{i}") for i in range(501)]
    res = app_client.post(
        "/api/v1/events:batch", json=_batch(events), headers=_auth(app_client)
    )
    assert res.status_code == 413
    assert "batch_events" in res.text


# --- 脱敏 ---

async def test_sensitive_fields_excluded_before_persist(app_client: TestClient) -> None:
    evt = _event(
        "redact-1",
        attributes={
            "exit_code": 137,
            "api_key": "sk-abcdef0123456789abcdef",
            "authorization": "Bearer sometoken1234567890",
            "cookie": "session=abc123; token=xyz",
            "system_prompt": "你是一个内部助手，请读取 /etc/passwd",
            "environment": {"SECRET_KEY": "supersecret", "PATH": "/usr/bin"},
            "contact": "用户 13800138000 的邮箱 user@example.com",
        },
    )
    res = app_client.post(
        "/api/v1/events:batch", json=_batch([evt]), headers=_auth(app_client)
    )
    assert res.status_code == 202, res.text
    assert res.json()["accepted"] == 1

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.models import Event

    # 独立引擎：不复用应用全局引擎（事件循环不同）
    engine = create_async_engine(os.environ["DATABASE_URL"])
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        row = (
            await session.execute(select(Event).where(Event.event_id == "redact-1"))
        ).scalar_one()
        payload = json.dumps(row.payload, ensure_ascii=False)
        # 禁止原文不得入库
        for banned in (
            "sk-abcdef0123456789abcdef",
            "sometoken1234567890",
            "session=abc123",
            "/etc/passwd",
            "supersecret",
            "13800138000",
            "user@example.com",
        ):
            assert banned not in payload, f"敏感原文泄漏：{banned}"
        # 被排除键留有审计记录
        assert "api_key" in row.payload.get("_redacted", {}).get("removed_keys", [])
        assert row.payload["exit_code"] == 137
    await engine.dispose()


# --- 幂等 ---

async def test_duplicate_events_counted(app_client: TestClient) -> None:
    batch = _batch([_event(f"idem-{i}") for i in range(3)])
    first = app_client.post("/api/v1/events:batch", json=batch, headers=_auth(app_client))
    assert first.status_code == 202
    assert first.json()["accepted"] == 3

    # 重发 10 次：事实表不新增
    for _ in range(10):
        again = app_client.post("/api/v1/events:batch", json=batch, headers=_auth(app_client))
        assert again.status_code == 202
        assert again.json()["accepted"] == 0
        assert again.json()["duplicate"] == 3


# --- 规则配置拒绝 ---

async def test_rule_config_rejections(tmp_path: Path) -> None:
    from apps.control_plane.diagnosis.rule_loader import RuleConfigError, load_rule

    base = {
        "schema_version": "1.0",
        "rule_id": "R9",
        "version": "1.0.0",
        "title": "测试",
        "symptoms": ["task.failed"],
        "window": {"before_seconds": 60, "after_seconds": 30},
        "evaluator": "r1_oom",
        "recommendations": ["x"],
    }

    def write(doc: dict) -> Path:
        import yaml

        p = tmp_path / "R9.yaml"
        p.write_text(yaml.safe_dump(doc), encoding="utf-8")
        return p

    # 未知 evaluator
    with pytest.raises(RuleConfigError, match="未注册"):
        load_rule(write(base | {"evaluator": "evil_evaluator"}))
    # 任意 Python
    with pytest.raises(RuleConfigError, match="禁止"):
        load_rule(write(base | {"python": "import os"}))
    # 任意 SQL
    with pytest.raises(RuleConfigError, match="禁止"):
        load_rule(write(base | {"sql": "SELECT * FROM events"}))
    # Schema 校验失败（缺 recommendations）
    bad = dict(base)
    bad.pop("recommendations")
    with pytest.raises(RuleConfigError, match="Schema"):
        load_rule(write(bad))


# --- 8 页面外壳 ---

async def test_eight_page_shells(app_client: TestClient) -> None:
    pages = [
        "/",
        "/pages/topology",
        "/pages/workloads",
        "/pages/tasks",
        "/pages/incidents",
        "/pages/diagnosis",
        "/pages/status",
        "/pages/lab",
    ]
    for path in pages:
        res = app_client.get(path)
        assert res.status_code == 200, path
        body = res.text
        assert "层迹 LayerTrace" in body
        for nav in ("总览", "拓扑", "工作负载", "任务", "事件单", "诊断", "状态", "实验"):
            assert nav in body, f"{path} 缺少导航 {nav}"


async def test_status_endpoint_honest(app_client: TestClient) -> None:
    res = app_client.get("/api/v1/status", headers=_auth(app_client))
    assert res.status_code == 200
    body = res.json()
    assert body["mode"] in ("prod", "lab", "replay")
    states = {s["type"]: s["state"] for s in body["sources"]}
    # 未配置 ZSvirt 必须 not_configured，禁止显示健康
    assert states.get("zsvirt") == "not_configured"
