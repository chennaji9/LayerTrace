"""Bearer 身份与服务端范围注入。

- tenant_id / project_id 一律由服务端根据认证主体写入，客户端不得覆盖
  （契约模型中不含这两个字段，覆盖企图在 Schema 层即被拒绝）。
- 主 API 使用配置的 bearer 令牌（恒定时间比较）；探针使用注册颁发的范围凭证
  （见 api/probes.py），本模块提供统一的 Principal 结构与依赖。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import time
import uuid
from dataclasses import dataclass, field

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.db import get_session


@dataclass
class Principal:
    """认证主体：范围由服务端决定。"""

    subject: str
    tenant_id: uuid.UUID
    project_id: uuid.UUID
    kind: str  # "api" | "probe"
    scopes: set[str] = field(default_factory=set)
    probe_id: uuid.UUID | None = None
    vm_id: uuid.UUID | None = None


_bearer = HTTPBearer(auto_error=False)


def _authenticate_token(token: str) -> Principal | None:
    settings = get_settings()
    for index, configured in enumerate(settings.api_token_list):
        if hmac.compare_digest(token, configured):
            return Principal(
                subject=f"api-token:{index}",
                tenant_id=uuid.UUID(settings.tenant_id),
                project_id=uuid.UUID(settings.project_id),
                kind="api",
                scopes={"read", "write", "admin"},
            )
    return None


SESSION_MAX_AGE = 12 * 60 * 60


def create_session_cookie(token: str, *, csrf_token: str) -> str:
    """Create a bounded, signed browser session without putting the API token in it."""
    if _authenticate_token(token) is None or not csrf_token:
        raise ValueError("无效登录凭证")
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "expires": int(time.time()) + SESSION_MAX_AGE,
                    "csrf": csrf_token,
                },
                separators=(",", ":"),
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    signature = hmac.new(
        token.encode(), b"layertrace-session-v1." + payload.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def _session_payload(value: str) -> tuple[Principal, dict] | None:
    if not value or len(value) > 4096:
        return None
    try:
        payload, signature = value.rsplit(".", 1)
        for token in get_settings().api_token_list:
            expected = hmac.new(
                token.encode(), b"layertrace-session-v1." + payload.encode(), hashlib.sha256
            ).hexdigest()
            if hmac.compare_digest(signature, expected):
                data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                if not isinstance(data, dict) or float(data.get("expires", 0)) <= time.time():
                    return None
                principal = _authenticate_token(token)
                return (principal, data) if principal is not None else None
    except (ValueError, TypeError, KeyError):
        return None
    return None


def verify_session_cookie(value: str) -> Principal | None:
    result = _session_payload(value)
    return result[0] if result else None


def verify_session_csrf(value: str, csrf_token: str) -> bool:
    result = _session_payload(value)
    return bool(
        result and csrf_token and hmac.compare_digest(str(result[1].get("csrf", "")), csrf_token)
    )


async def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    settings = get_settings()
    # 演示/回放模式：未配置任何令牌时不强制认证（quickstart 的 curl 与页面直接可用）；
    # 一旦配置令牌则严格执行（安全测试覆盖 401 路径）。
    if not settings.api_token_list and settings.mode == "replay":
        try:
            local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
        except ValueError:
            local = bool(request.client and request.client.host == "testclient")
        if not local:
            raise HTTPException(
                status_code=401, detail={"code": "unauthorized", "message": "回放匿名入口仅限本机"}
            )
        return Principal(
            subject="anonymous-demo",
            tenant_id=uuid.UUID(settings.tenant_id),
            project_id=uuid.UUID(settings.project_id),
            kind="api",
            scopes={"read", "write"},
        )
    if credentials is None or not credentials.credentials:
        cookie = request.cookies.get("layertrace_session", "")
        principal = verify_session_cookie(cookie)
        if principal is not None:
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                csrf = request.headers.get("X-CSRF-Token", "")
                if not verify_session_csrf(cookie, csrf) or not hmac.compare_digest(
                    csrf, request.cookies.get("layertrace_csrf", "")
                ):
                    raise HTTPException(
                        status_code=403,
                        detail={"code": "csrf_failed", "message": "会话写操作缺少有效 CSRF 凭证"},
                    )
            return principal
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "缺少或无效的认证凭证"},
        )
    principal = _authenticate_token(credentials.credentials)
    if principal is None:
        from apps.control_plane.api.probes import authenticate_probe

        principal = await authenticate_probe(session, credentials.credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "unauthorized", "message": "缺少或无效的认证凭证"},
        )
    return principal


def require_scope(scope: str):
    """要求主体具备指定权限范围。"""

    async def _check(principal: Principal = Depends(get_principal)) -> Principal:
        if scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "forbidden", "message": f"缺少权限范围 {scope}"},
            )
        return principal

    return _check


def ensure_resource_scope(principal: Principal, resource_project_id) -> bool:
    """资源范围检查：主体只能访问其项目范围内的资源。"""
    return str(resource_project_id) == str(principal.project_id)


# 供测试注入主体
_principal_override: Principal | None = None


def set_principal_override(principal: Principal | None) -> None:
    global _principal_override
    _principal_override = principal


async def get_principal_with_override(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Principal:
    if _principal_override is not None:
        return _principal_override
    return await get_principal(request, credentials, session)
