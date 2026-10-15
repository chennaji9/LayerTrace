"""探针注册、凭证签发与心跳 API（T081）。

- 短期注册令牌（PROBE_REGISTRATION_TOKEN）绑定安装 ID 与 VM；
- 注册签发可轮换的探针凭证（哈希存 SourceStatus.capabilities，不存明文）；
- 心跳更新健康、积压、丢弃与时钟状态；
- 越权（注册令牌错误 / 探针不存在）返回 403/404。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.api.errors import ApiError
from apps.control_plane.auth import Principal, get_principal, require_scope
from apps.control_plane.config import get_settings
from apps.control_plane.db import get_session
from apps.control_plane.models import Resource, SourceStatus
from packages.contracts.schemas import (
    EventBatch,
    ProbeHeartbeat,
    ProbeRegistration,
    ProbeRegistrationResult,
)

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])

_bearer = HTTPBearer(auto_error=False)


def _registration_token() -> str | None:
    return (
        os.environ.get("PROBE_REGISTRATION_SECRET")
        or os.environ.get("PROBE_REGISTRATION_TOKEN")
        or None
    )


def _hash_credential(credential: str) -> str:
    return hashlib.sha256(credential.encode()).hexdigest()


async def _upsert_vm_resource(
    session: AsyncSession, *, tenant_id: uuid.UUID, project_id: uuid.UUID, vm_source_id: str
) -> Resource:
    now = datetime.now(UTC)
    vm = (
        await session.execute(
            select(Resource).where(
                Resource.project_id == project_id,
                Resource.provider == "zsvirt",
                Resource.source_id == vm_source_id,
                Resource.kind == "vm",
            )
        )
    ).scalar_one_or_none()
    if vm is None:
        if get_settings().zsvirt_enabled:
            raise ApiError(
                403, "unknown_vm", "真实平台模式须先由平台同步确认 VM UUID，探针不能自建平台身份"
            )
        vm = Resource(
            tenant_id=tenant_id,
            project_id=project_id,
            kind="vm",
            provider="zsvirt",
            provider_instance="default",
            source_id=vm_source_id,
            display_name=vm_source_id,
            provenance="manual",
            first_seen=now,
            last_seen=now,
        )
        session.add(vm)
        await session.flush()
    else:
        vm.last_seen = now
    return vm


async def register_probe_core(
    session: AsyncSession,
    *,
    installation_id: str,
    vm_source_id: str,
    profile: str,
    tenant_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    capabilities: dict | None = None,
    provided_registration_token: str | None = None,
) -> dict:
    settings = get_settings()
    tenant_id = tenant_id or uuid.UUID(settings.tenant_id)
    project_id = project_id or uuid.UUID(settings.project_id)

    expected = _registration_token()
    if expected is None:
        raise ApiError(403, "registration_disabled", "未配置探针注册凭证，注册入口关闭")
    if provided_registration_token is None or not hmac.compare_digest(
        provided_registration_token, expected
    ):
        raise ApiError(403, "forbidden", "注册令牌无效")

    vm = await _upsert_vm_resource(
        session, tenant_id=tenant_id, project_id=project_id, vm_source_id=vm_source_id
    )

    # 安装 ID 绑定检查：同一安装 ID 已绑定其他 VM 时要求重新注册（409 由调用方映射）
    existing = (
        await session.execute(
            select(SourceStatus).where(
                SourceStatus.source_type == "probe",
                SourceStatus.capabilities["installation_id"].astext == installation_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        bound_vm = existing.capabilities.get("vm_source_id")
        if bound_vm != vm_source_id:
            raise ApiError(409, "conflict", "安装 ID 已绑定到其他 VM，必须重新注册")
        if existing.capabilities.get("credential_revoked_at"):
            raise ApiError(403, "probe_revoked", "该安装的探针凭证已撤销，请由管理员重新部署")

    credential = secrets.token_urlsafe(32)
    probe_id = existing.id if existing is not None else uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=settings.probe_token_ttl_seconds)
    if existing is None:
        session.add(
            SourceStatus(
                id=probe_id,
                source_type="probe",
                source_instance=str(probe_id),
                state="healthy",
                last_success_at=datetime.now(UTC),
                capabilities={
                    "installation_id": installation_id,
                    "vm_source_id": vm_source_id,
                    "vm_id": str(vm.id),
                    "profile": profile,
                    "credential_sha256": _hash_credential(credential),
                    "credential_expires_at": expires_at.isoformat(),
                },
            )
        )
    else:
        existing.capabilities = {
            **existing.capabilities,
            "credential_sha256": _hash_credential(credential),
            "credential_expires_at": expires_at.isoformat(),
            "profile": profile,
        }
        existing.state = "healthy"
        existing.last_success_at = datetime.now(UTC)
        existing.error_code = None
        existing.error_summary = None
    await session.flush()
    return {
        "probe_id": str(probe_id),
        "vm_id": str(vm.id),
        "credential": credential,
        "expires_at": expires_at,
    }


async def heartbeat_core(
    session: AsyncSession,
    *,
    probe_id: str,
    observed_at: datetime,
    queue_depth: int,
    dropped_count: int,
    oldest_queued_at: datetime | None = None,
    clock_offset_seconds: float | None = None,
    gaps: list | None = None,
    provided_credential: str | None = None,
) -> None:
    status = (
        await session.execute(
            select(SourceStatus).where(
                SourceStatus.source_type == "probe",
                SourceStatus.source_instance == probe_id,
            )
        )
    ).scalar_one_or_none()
    if status is None:
        raise ApiError(404, "not_found", "探针不存在")
    if provided_credential is None:
        raise ApiError(403, "forbidden", "心跳必须提供探针凭证")
    if provided_credential is not None:
        expected_hash = status.capabilities.get("credential_sha256")
        if not expected_hash or not hmac.compare_digest(
            _hash_credential(provided_credential), expected_hash
        ):
            raise ApiError(403, "forbidden", "探针凭证无效")
        expires = status.capabilities.get("credential_expires_at")
        if not expires or datetime.fromisoformat(expires) <= datetime.now(UTC):
            raise ApiError(401, "credential_expired", "探针凭证已过期，请重新注册")

    now = datetime.now(UTC)
    status.state = (
        "healthy" if clock_offset_seconds is None or abs(clock_offset_seconds) <= 5 else "degraded"
    )
    status.last_success_at = now
    status.updated_at = now
    status.latency_ms = (
        max(0, int((now - observed_at).total_seconds() * 1000)) if observed_at <= now else 0
    )
    status.capabilities = {
        **status.capabilities,
        "queue_depth": queue_depth,
        "clock_offset_seconds": clock_offset_seconds,
    }
    status.dropped_count = dropped_count
    queue_lag = None
    if oldest_queued_at is not None:
        queue_lag = max(0.0, (now - oldest_queued_at).total_seconds())
    status.queue_lag_seconds = queue_lag
    if gaps:
        existing_gaps = list(status.data_gaps or [])
        existing_gaps.extend(gaps)
        status.data_gaps = existing_gaps[-100:]
    await session.flush()


@router.post(
    "/probes/register",
    status_code=201,
    response_model=ProbeRegistrationResult,
    include_in_schema=False,
)
@router.post("/probes:register", status_code=201, response_model=ProbeRegistrationResult)
async def register_probe(
    request: ProbeRegistration,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> ProbeRegistrationResult:
    result = await register_probe_core(
        session,
        installation_id=request.installation_id,
        vm_source_id=request.vm_ref.external_id,
        profile=request.profile,
        capabilities=dict(request.capabilities),
        provided_registration_token=credentials.credentials if credentials else None,
    )
    return ProbeRegistrationResult(**result)


@router.post("/probes/{probe_id}/heartbeat", status_code=204, include_in_schema=False)
@router.post("/probes/{probe_id}:heartbeat", status_code=204)
async def heartbeat_probe(
    probe_id: uuid.UUID,
    request: ProbeHeartbeat,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await heartbeat_core(
        session,
        probe_id=str(probe_id),
        observed_at=request.observed_at,
        queue_depth=request.queue_depth,
        dropped_count=request.dropped_count,
        oldest_queued_at=request.oldest_queued_at,
        clock_offset_seconds=request.clock_offset_seconds,
        gaps=request.model_dump(mode="json")["gaps"],
        provided_credential=credentials.credentials if credentials else None,
    )
    return Response(status_code=204)


@router.post("/probes/{probe_id}:revoke", status_code=204)
@router.post("/probes/{probe_id}/revoke", status_code=204, include_in_schema=False)
async def revoke_probe(
    probe_id: uuid.UUID,
    principal: Principal = Depends(require_scope("admin")),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if principal.kind != "api" or "write" not in principal.scopes:
        raise ApiError(403, "forbidden", "仅管理员可撤销探针凭证")
    source = (
        await session.execute(
            select(SourceStatus).where(
                SourceStatus.id == probe_id, SourceStatus.source_type == "probe"
            )
        )
    ).scalar_one_or_none()
    vm_id = (source.capabilities or {}).get("vm_id") if source else None
    try:
        vm = await session.get(Resource, uuid.UUID(vm_id)) if vm_id else None
    except (ValueError, TypeError):
        vm = None
    if vm is None or vm.project_id != principal.project_id or vm.tenant_id != principal.tenant_id:
        raise ApiError(404, "not_found", "探针不存在或不在认证范围内")
    now = datetime.now(UTC)
    capabilities = dict(source.capabilities or {})
    capabilities.pop("credential_sha256", None)
    capabilities["credential_expires_at"] = now.isoformat()
    capabilities["credential_revoked_at"] = now.isoformat()
    capabilities["credential_revoked_by"] = principal.subject
    source.capabilities = capabilities
    source.state = "unavailable"
    source.error_code = "credential_revoked"
    source.error_summary = "探针凭证已由管理员撤销，需重新注册"
    source.updated_at = now
    await session.flush()
    return Response(status_code=204)


async def authenticate_probe(session: AsyncSession, credential: str) -> Principal | None:
    """Resolve a nonexpired collector credential to its authoritative VM scope."""
    digest = _hash_credential(credential)
    source = (
        await session.execute(
            select(SourceStatus).where(
                SourceStatus.source_type == "probe",
                SourceStatus.capabilities["credential_sha256"].astext == digest,
            )
        )
    ).scalar_one_or_none()
    if source is None:
        return None
    expires = source.capabilities.get("credential_expires_at")
    try:
        if not expires or datetime.fromisoformat(expires) <= datetime.now(UTC):
            return None
        vm = await session.get(Resource, uuid.UUID(source.capabilities["vm_id"]))
    except (ValueError, KeyError, TypeError):
        return None
    if vm is None:
        return None
    return Principal(
        subject=f"probe:{source.id}",
        tenant_id=vm.tenant_id,
        project_id=vm.project_id,
        kind="probe",
        scopes={"write"},
        probe_id=source.id,
        vm_id=vm.id,
    )


class ProbeContainer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    container_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str = Field(max_length=256)
    service_name: str | None = Field(default=None, max_length=256)
    instance_id: str | None = Field(default=None, max_length=512)
    started_at: str | None = Field(default=None, max_length=64)
    created_at: str | None = Field(default=None, max_length=64)
    state: str = Field(
        default="unknown",
        pattern="^(running|stopped|exited|created|paused|restarting|dead|unknown)$",
    )
    limits: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    image_digest: str | None = Field(default=None, max_length=512)
    labels: dict = Field(default_factory=dict)


class ProbeDependency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_instance_id: str = Field(max_length=512)
    to_instance_id: str = Field(max_length=512)


class ProbeInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observed_at: datetime
    containers: list[ProbeContainer] = Field(default_factory=list, max_length=500)
    dependencies: list[ProbeDependency] = Field(default_factory=list, max_length=500)
    complete: bool = True

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("observed_at 必须含时区")
        return value


@router.post("/probes/{probe_id}:inventory")
@router.post("/probes/{probe_id}/inventory", include_in_schema=False)
async def ingest_probe_inventory(
    probe_id: uuid.UUID,
    request: ProbeInventory,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if principal.kind != "probe" or principal.probe_id != probe_id:
        raise ApiError(403, "forbidden", "探针只能上报自己的库存")
    from apps.control_plane.inventory.probe_service import apply_probe_inventory

    return await apply_probe_inventory(session, principal=principal, inventory=request)


@router.post("/probes/{probe_id}/events", status_code=202, include_in_schema=False)
async def ingest_probe_events(
    probe_id: uuid.UUID,
    batch: EventBatch,
    principal: Principal = Depends(get_principal),
    session: AsyncSession = Depends(get_session),
):
    if principal.kind != "probe" or principal.probe_id != probe_id:
        raise ApiError(403, "forbidden", "探针只能上报自己的事件")
    from apps.control_plane.api.events import ingest_events

    return await ingest_events(batch, principal, session)
