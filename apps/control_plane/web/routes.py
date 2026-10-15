"""页面路由。

页面是服务端渲染的外壳；所有数据由浏览器通过 /api/v1/* 获取（FR-009），
模板与路由不得读取数据库、剧本或 fixture。8 个页面：
总览 / 拓扑 / 工作负载 / 任务 / 事件单 / 诊断 / 状态 / 实验。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from apps.control_plane.config import get_settings

router = APIRouter(include_in_schema=False)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

PAGES: list[tuple[str, str, str]] = [
    # (路径, 模板, 页面名)
    ("/", "overview/index.html", "overview"),
    ("/pages/topology", "topology/index.html", "topology"),
    ("/pages/workloads", "workloads/index.html", "workloads"),
    ("/pages/tasks", "tasks/index.html", "tasks"),
    ("/pages/incidents", "incidents/index.html", "incidents"),
    ("/pages/diagnosis", "diagnosis/index.html", "diagnosis"),
    ("/pages/status", "status/index.html", "status"),
    ("/pages/lab", "lab/index.html", "lab"),
]


def _render(request: Request, template_name: str, page: str) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        template_name,
        {"page": page, "mode": settings.mode, "lab_enabled": settings.lab_enabled},
    )


def register_web_routes(app) -> None:  # noqa: ANN001 - FastAPI app
    from fastapi.staticfiles import StaticFiles

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    app.include_router(router)
    from apps.control_plane.web.session import register_session_routes

    register_session_routes(app, templates)


for _path, _template, _page in PAGES:

    def _make(template_name: str, page: str):
        async def page_route(request: Request) -> HTMLResponse:
            return _render(request, template_name, page)

        page_route.__name__ = f"page_{page}"
        return page_route

    router.get(_path, response_class=HTMLResponse, name=_page)(_make(_template, _page))
