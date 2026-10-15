"""Project-scoped durable evidence lookup for every diagnosis revision."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.auth import Principal, require_scope
from apps.control_plane.db import get_session
from apps.control_plane.models import Evidence
from packages.contracts.schemas import EvidenceOut

router = APIRouter(prefix="/api/v1", tags=["Incidents"])


def evidence_out(row: Evidence) -> EvidenceOut:
    return EvidenceOut(
        id=str(row.id),
        kind=row.kind,
        resource_id=str(row.resource_id) if row.resource_id else None,
        range_start=row.range_start,
        range_end=row.range_end,
        source_ref=row.source_ref,
        summary=dict(row.summary or {}),
        query_parameters=dict(row.query_parameters or {}),
        provenance=row.provenance,
        source_expired=row.source_expired,
    )


@router.get("/evidence/{evidence_id}", response_model=EvidenceOut)
async def get_evidence(
    evidence_id: uuid.UUID,
    principal: Principal = Depends(require_scope("read")),
    session: AsyncSession = Depends(get_session),
) -> EvidenceOut:
    row = (
        await session.execute(
            select(Evidence).where(
                Evidence.id == evidence_id, Evidence.project_id == principal.project_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(404, "not_found", "对象不存在或不在认证范围内")
    return evidence_out(row)
