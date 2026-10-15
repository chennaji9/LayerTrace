"""跨主机传输安全中间件。

规则（FR-028、SC-011）：
- 仅同一主机的 loopback 客户端允许明文 HTTP；
- 跨主机连接必须以 HTTPS 到达（经 Caddy TLS 终止后由可信代理转发）；
- 只信任来自可信代理（Caddy 网段）的 X-Forwarded-Proto 头；其余来源伪造的
  转发头一律剥离并忽略；
- 违反时返回 426 + 传输安全错误体，不进入业务处理。
"""

from __future__ import annotations

import ipaddress
from typing import Any

from apps.control_plane.config import get_settings

FORWARDED_HEADERS = (b"x-forwarded-proto", b"x-forwarded-host", b"x-forwarded-for", b"x-forwarded-port")


def _client_ip(scope: dict[str, Any]) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def _is_loopback(ip: str | None) -> bool:
    if ip is None:
        # 无客户端地址（进程内测试传输）视同本地
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # 非 IP 形式（如 ASGI 测试客户端的 "testclient"）视同本地进程
        return True
    return addr.is_loopback


def _is_trusted_proxy(ip: str | None) -> bool:
    if ip is None:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in get_settings().trusted_proxy_list:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


def _is_allowed_plaintext_client(ip: str | None) -> bool:
    """Allow an explicitly configured container gateway for host-loopback DNAT."""
    if ip is None:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in get_settings().plaintext_client_cidr_list:
        try:
            if addr in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            continue
    return False


class TransportSecurityMiddleware:
    """纯 ASGI 中间件：传输上下文校验与不可信转发头剥离。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        settings = get_settings()
        client_ip = _client_ip(scope)
        loopback = _is_loopback(client_ip)
        allowed_plaintext = _is_allowed_plaintext_client(client_ip)
        trusted = _is_trusted_proxy(client_ip)

        headers = list(scope.get("headers", []))
        forwarded_proto: str | None = None
        if trusted:
            for name, value in headers:
                if name == b"x-forwarded-proto":
                    forwarded_proto = value.decode("latin-1").strip()
                    break
        else:
            # 剥离不可信来源的转发头
            headers = [(name, value) for name, value in headers if name not in FORWARDED_HEADERS]
            scope["headers"] = headers

        effective_scheme = forwarded_proto or scope.get("scheme", "http")
        off_loopback = settings.require_tls_off_loopback and not (loopback or allowed_plaintext)

        if effective_scheme != "https" and off_loopback:
            await self._reject_plaintext(scope, send)
            return

        # 将有效协议写入 scope，供应用与文档生成使用
        scope["scheme"] = effective_scheme
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject_plaintext(scope: dict[str, Any], send: Any) -> None:
        body = (
            '{"code":"transport_security","message":'
            '"跨主机连接必须使用 HTTPS；仅同一主机 loopback 开发入口允许 HTTP"}'
        ).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 426,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
