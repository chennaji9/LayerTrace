"""标准事件批量写入 API。

- Schema 校验（含 256 KiB / 8 MiB / 500 条 / 64 属性 / 8 层深度超限整批 413）；
- 认证与范围：tenant/project 由服务端注入；
- 入队前脱敏与幂等写入（event_id 去重）；
- 202 返回 accepted/duplicate/unresolved/rejected 计数。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.auth import Principal, require_scope
from apps.control_plane.db import get_session
from apps.control_plane.ingestion import repository
from apps.control_plane.models import Resource
from packages.contracts.schemas import EventBatch, IngestionResult

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])


@router.post(
    "/events:batch",
    status_code=202,
    response_model=IngestionResult,
    responses={
        413: {
            "description": "单条事件超过 256 KiB、批次超过 8 MiB/500 条、属性超过 64 项或嵌套超过 8 层"
        }
    },
)
async def ingest_events(
    batch: EventBatch,
    principal: Principal = Depends(require_scope("write")),
    session: AsyncSession = Depends(get_session),
) -> IngestionResult:
    if principal.kind == "probe":
        for event in batch.events:
            key = (event.resource_ref.namespace, event.resource_ref.external_id)
            mapping = await repository._resolve_aliases(
                session,
                {key},
                tenant_id=principal.tenant_id,
                project_id=principal.project_id,
                at=event.occurred_at,
            )
            resource_id = mapping.get(key)
            if resource_id is None:
                # Early Docker create facts may predate first inventory. They may
                # be stored unresolved, but only for a previously registered VM object.
                current = await repository._resolve_aliases(
                    session, {key}, tenant_id=principal.tenant_id, project_id=principal.project_id
                )
                resource_id = current.get(key)
                if resource_id is None:
                    raise ApiError(
                        403, "unknown_probe_resource", "探针须先上报绑定 VM 的库存，再上报事件"
                    )
            resource = await session.get(Resource, resource_id)
            if resource_id != principal.vm_id and str(
                (resource.attributes or {}).get("vm_id")
            ) != str(principal.vm_id):
                raise ApiError(403, "forbidden", "探针不可写入其他 VM 的资源")
    return await repository.ingest_batch(
        session,
        tenant_id=principal.tenant_id,
        project_id=principal.project_id,
        batch=batch,
    )
