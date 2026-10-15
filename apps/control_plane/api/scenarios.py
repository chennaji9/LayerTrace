"""场景运行 / 验证 / 恢复 API（T058）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.auth import Principal, require_scope
from apps.control_plane.db import get_session
from apps.control_plane.integrations import scenario_service
from apps.control_plane.integrations.scenario_service import SCENARIO_NAME_RE  # noqa: F401
from packages.contracts.schemas import ScenarioRunOut, ScenarioRunRequest

router = APIRouter(prefix="/api/v1", tags=["Scenarios"])


def _to_out(run: dict) -> ScenarioRunOut:
    return ScenarioRunOut(
        id=run["id"],
        scenario_name=run["scenario_name"],
        mode=run["mode"],
        state=run["state"],
        started_at=run["started_at"],
        deadline_at=run["deadline_at"],
        ended_at=run["ended_at"],
        verification_result=run.get("verification_result"),
        restore_result=run.get("restore_result"),
    )


@router.post(
    "/scenarios/{scenario_name}:run",
    status_code=202,
    response_model=ScenarioRunOut,
    responses={
        "404": {"description": "剧本不存在"},
        "403": {"description": "非 lab 模式、目标未授权或缺少能力"},
        "409": {"description": "同一目标已有活动场景"},
    },
)
async def run_scenario(
    scenario_name: str,
    request: ScenarioRunRequest,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRunOut:
    try:
        run = await scenario_service.run_scenario(
            session,
            scenario_name,
            mode=request.mode,
            started_by=principal.subject,
            timeout_seconds=request.timeout_seconds,
        )
    except scenario_service.ScenarioError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    return _to_out(run)


@router.post("/scenario-runs/{run_id}:verify", response_model=ScenarioRunOut)
async def verify_scenario(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRunOut:
    try:
        run = await scenario_service.verify_scenario(session, str(run_id))
    except scenario_service.ScenarioError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    return _to_out(run)


@router.post("/scenario-runs/{run_id}:restore", status_code=202, response_model=ScenarioRunOut)
async def restore_scenario(
    run_id: uuid.UUID,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> ScenarioRunOut:
    try:
        run = await scenario_service.restore_scenario(session, str(run_id))
    except scenario_service.ScenarioError as exc:
        raise ApiError(exc.status_code, exc.code, exc.message) from exc
    return _to_out(run)
