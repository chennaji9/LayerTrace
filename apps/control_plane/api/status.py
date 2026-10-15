"""数据源状态接口（T085）。

依赖失效仍返回 200，并在各数据源 state 中诚实表达（openapi getStatus）。
完整汇总（连接状态、最后成功、延迟、错误、权限、能力、未解析、积压、丢弃）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.config import get_settings
from apps.control_plane.db import get_session
from apps.control_plane.integrations.status_service import summarize
from packages.contracts.schemas import SourceStatusOut, StatusResponse

router = APIRouter(prefix="/api/v1", tags=["Status"])


@router.get("/status", response_model=StatusResponse)
async def get_status(
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> StatusResponse:
    settings = get_settings()
    summary = await summarize(session, project_id=principal.project_id)
    await session.commit()

    sources = [
        SourceStatusOut(
            type=s["type"],
            instance=s["instance"],
            state=s["state"],  # type: ignore[arg-type]
            last_success_at=s["last_success_at"],
            latency_ms=s["latency_ms"],
            error_summary=s["error_summary"],
            missing_permissions=s["missing_permissions"],
            capabilities=s["capabilities"],
            unresolved_count=s["unresolved_count"],
            queue_lag_seconds=s["queue_lag_seconds"],
            dropped_count=s["dropped_count"],
            data_gaps=s["data_gaps"],
        )
        for s in summary["sources"]
    ]
    return StatusResponse(
        generated_at=datetime.now(UTC),
        mode=settings.mode,  # type: ignore[arg-type]
        sources=sources,
        unresolved_count=summary["unresolved_count"],
        diagnosis_error_count=0,
    )
