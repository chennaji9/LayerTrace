"""资源查询 API（T068）：kind/provenance 过滤与稳定游标分页。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.pagination import apply_descending_cursor, encode_cursor
from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.db import get_session
from apps.control_plane.models import Resource
from packages.contracts.schemas import ResourceOut, ResourcePage

router = APIRouter(prefix="/api/v1", tags=["Inventory"])


@router.get("/resources", response_model=ResourcePage)
async def list_resources(
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    provenance: str | None = Query(default=None),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> ResourcePage:
    query = select(Resource).where(Resource.project_id == principal.project_id)
    if kind:
        query = query.where(Resource.kind == kind)
    if provenance:
        query = query.where(Resource.provenance == provenance)
    query = apply_descending_cursor(query, Resource.last_seen, Resource.id, cursor)
    query = query.order_by(Resource.last_seen.desc(), Resource.id.desc()).limit(limit + 1)

    rows = (await session.execute(query)).scalars().all()
    page = rows[:limit]
    next_cursor = (
        encode_cursor(page[-1].last_seen.isoformat(), str(page[-1].id))
        if len(rows) > limit and page
        else None
    )
    return ResourcePage(
        items=[
            ResourceOut(
                id=str(r.id),
                kind=r.kind,
                provider=r.provider,
                provider_instance=r.provider_instance,
                source_id=r.source_id,
                display_name=r.display_name,
                attributes=dict(r.attributes or {}),
                provenance=r.provenance,  # type: ignore[arg-type]
                first_seen=r.first_seen,
                last_seen=r.last_seen,
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )
