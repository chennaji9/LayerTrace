"""拓扑 API（T058 当前拓扑；T069 扩展历史查询）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.auth import Principal, get_principal
from apps.control_plane.db import get_session
from apps.control_plane.inventory import temporal_graph
from apps.control_plane.models import Relationship, Resource
from packages.contracts.schemas import RelationshipOut, ResourceOut, TopologyResponse

router = APIRouter(prefix="/api/v1", tags=["Inventory"])


def _resource_out(r: Resource) -> ResourceOut:
    return ResourceOut(
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


def _relationship_out(rel: Relationship) -> RelationshipOut:
    return RelationshipOut(
        id=str(rel.id),
        from_id=str(rel.from_id),
        type=rel.type,
        to_id=str(rel.to_id),
        valid_from=rel.valid_from,
        valid_to=rel.valid_to,
        source=rel.source,
        provenance=rel.provenance,  # type: ignore[arg-type]
    )


@router.get("/topology", response_model=TopologyResponse)
async def get_topology(
    at: datetime | None = Query(default=None, description="省略时返回当前拓扑"),
    root_id: uuid.UUID | None = Query(default=None),
    max_depth: int = Query(default=5, ge=1, le=8),
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> TopologyResponse:
    at = at or datetime.now(UTC)
    nodes, edges, truncated = await temporal_graph.effective_topology(
        session,
        principal.project_id,
        at,
        root_id=root_id,
        max_depth=max_depth,
    )
    return TopologyResponse(
        at=at,
        nodes=[_resource_out(n) for n in nodes],
        edges=[_relationship_out(e) for e in edges],
        truncated=truncated,
    )
