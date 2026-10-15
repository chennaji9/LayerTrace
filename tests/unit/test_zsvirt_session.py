"""Session refresh is bounded and never mistakes permission failures for expiration."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from integrations.zsvirt.provider import _SESSION_CACHE, ZSvirtAuthError, ZSvirtProvider


@pytest.fixture(autouse=True)
def isolate_session_cache():
    _SESSION_CACHE.clear()
    yield
    _SESSION_CACHE.clear()


@pytest.mark.parametrize("expiry_status", [401, 503])
async def test_expired_session_logs_in_and_retries_read_once(expiry_status):
    calls = []
    expires = (datetime.now(UTC) + timedelta(days=1)).replace(microsecond=0)
    platform_expiry = expires.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
        "%b %d, %Y %I:%M:%S %p"
    )

    def handle(request):
        calls.append((request.method, request.url.path, request.headers.get("Authorization")))
        if request.url.path.endswith("/accounts/login"):
            assert b"logInByAccount" in request.content and b"readonly-hash" in request.content
            assert "old-session" not in request.headers.get("Authorization", "")
            return httpx.Response(
                200,
                json={"inventory": {"uuid": "new-session", "expiredDate": platform_expiry}},
            )
        if request.headers.get("Authorization") == "OAuth old-session":
            return httpx.Response(expiry_status, json={"error": {"code": "ID.1001"}})
        return httpx.Response(200, json={"inventories": []})

    provider = ZSvirtProvider(
        base_url="http://localhost",
        token="old-session",
        account_name="observer",
        password_hash="readonly-hash",
        transport=httpx.MockTransport(handle),
    )
    await provider.discover()
    assert [x[0] for x in calls] == ["GET", "PUT", "GET", "GET"]
    assert provider.health().state == "healthy"
    assert provider.capabilities().notes["session_expires_at"] == expires.isoformat()
    # A new worker adapter reuses the fresh in-memory session instead of logging in every poll.
    second = ZSvirtProvider(
        base_url="http://localhost",
        token="old-session",
        account_name="observer",
        password_hash="readonly-hash",
        transport=httpx.MockTransport(handle),
    )
    await second.discover()
    assert sum(method == "PUT" for method, _, _ in calls) == 1


async def test_token_only_expiry_sets_expired_state_without_login():
    def handle(request):
        assert request.method == "GET"
        return httpx.Response(503, json={"error": {"code": "ID.1001", "details": "secret-value"}})

    provider = ZSvirtProvider(
        base_url="http://localhost", token="expired", transport=httpx.MockTransport(handle)
    )
    with pytest.raises(ZSvirtAuthError) as exc:
        await provider.discover()
    assert "secret-value" not in str(exc.value)
    assert provider.health().state == "permission_denied"
    assert provider.health().error_code == "session_expired"


async def test_authorization_denial_does_not_retry_login():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(503, json={"error": {"code": "SYS.1019"}})

    provider = ZSvirtProvider(
        base_url="http://localhost",
        token="valid",
        account_name="observer",
        password_hash="readonly-hash",
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(ZSvirtAuthError):
        await provider.discover()
    assert calls == ["GET"]
    assert provider.health().error_code == "authorization_failed"


async def test_repeated_expiry_retries_only_once():
    calls = []

    def handle(request):
        calls.append(request.method)
        if request.method == "PUT":
            return httpx.Response(200, json={"inventory": {"uuid": "still-invalid"}})
        return httpx.Response(401, json={"error": {"code": "ID.1001"}})

    provider = ZSvirtProvider(
        base_url="http://localhost",
        token="old-session",
        account_name="observer",
        password_hash="readonly-hash",
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(ZSvirtAuthError):
        await provider.discover()
    assert calls == ["GET", "PUT", "GET"]


async def test_account_login_works_without_initial_token():
    calls = []

    def handle(request):
        calls.append(request.method)
        return httpx.Response(
            200,
            json={"inventory": {"uuid": "fresh"}}
            if request.method == "PUT"
            else {"inventories": []},
        )

    provider = ZSvirtProvider(
        base_url="http://localhost",
        token=None,
        account_name="observer",
        password_hash="readonly-hash",
        transport=httpx.MockTransport(handle),
    )
    assert provider.enabled
    await provider.discover()
    assert calls == ["PUT", "GET", "GET"]
