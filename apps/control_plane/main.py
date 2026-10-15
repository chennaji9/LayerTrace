"""LayerTrace 控制面 API 入口。

HTTP 服务仅绑定 loopback（见 config.WEB_HOST）；跨主机访问必须经 Caddy TLS 入口。
轮询/调度逻辑只存在于 worker 进程，本进程不执行任何轮询。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.control_plane.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        from apps.control_plane.db import dispose_engine

        await dispose_engine()

    app = FastAPI(
        title="LayerTrace Control Plane API",
        version="1.0.0",
        lifespan=lifespan,
        servers=[
            {
                "url": "http://localhost:8000",
                "description": "仅限同一主机 loopback 的开发和回放验证",
            },
            {
                "url": "https://layertrace.local",
                "description": "跨主机部署入口，必须验证受信证书、主机名和有效期",
            },
        ],
    )

    @app.get("/healthz", tags=["Status"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": settings.mode}

    # 中间件（传输安全最外层）与统一错误体
    from apps.control_plane.middleware.transport_security import TransportSecurityMiddleware

    app.add_middleware(TransportSecurityMiddleware)

    from apps.control_plane.api.errors import register_exception_handlers

    register_exception_handlers(app)

    # API 路由（/api/v1/*）
    from apps.control_plane.api import register_routers

    register_routers(app)

    # 页面路由（8 页外壳 + 静态资源）
    from apps.control_plane.web.routes import register_web_routes

    register_web_routes(app)

    return app


app = create_app()
