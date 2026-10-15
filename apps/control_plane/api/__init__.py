"""API 路由注册。

所有对外机器可读接口位于 /api/v1/*，与 specs 契约 openapi.yaml 一一对应。
新增路由模块时在此追加 import 并挂载。
"""

from __future__ import annotations

from fastapi import FastAPI


def register_routers(app: FastAPI) -> None:
    from apps.control_plane.api.events import router as events_router
    from apps.control_plane.api.evidence import router as evidence_router
    from apps.control_plane.api.incidents import router as incidents_router
    from apps.control_plane.api.probes import router as probes_router
    from apps.control_plane.api.resources import router as resources_router
    from apps.control_plane.api.scenarios import router as scenarios_router
    from apps.control_plane.api.status import router as status_router
    from apps.control_plane.api.tasks import router as tasks_router
    from apps.control_plane.api.topology import router as topology_router
    from apps.control_plane.api.workloads import router as workloads_router

    app.include_router(evidence_router)
    app.include_router(events_router)
    app.include_router(incidents_router)
    app.include_router(probes_router)
    app.include_router(resources_router)
    app.include_router(scenarios_router)
    app.include_router(status_router)
    app.include_router(tasks_router)
    app.include_router(topology_router)
    app.include_router(workloads_router)
