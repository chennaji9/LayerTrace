"""US4 集成测试：探针心跳 → stale/unknown/gap 状态与补传。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.models import Resource, SourceStatus

pytestmark = [pytest.mark.integration]


async def _session(test_database_url: str):
    engine = create_async_engine(test_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _register_vm(test_database_url: str, tag: str) -> dict:
    from apps.control_plane.api import probes as probes_api

    engine, maker = await _session(test_database_url)
    async with maker() as session:
        vm = Resource(
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            kind="vm",
            provider="zsvirt",
            provider_instance="default",
            source_id=f"{tag}-vm",
            display_name="vm",
            provenance="observed",
            first_seen=datetime.now(UTC),
            last_seen=datetime.now(UTC),
        )
        session.add(vm)
        await session.commit()
        result = await probes_api.register_probe_core(
            session,
            installation_id=f"{tag}-install",
            vm_source_id=vm.source_id,
            profile="lightweight",
            provided_registration_token="probe-staleness-secret",
        )
        await session.commit()
    await engine.dispose()
    return result


async def test_heartbeat_then_stale(test_database_url, clean_db, monkeypatch) -> None:
    monkeypatch.setenv("PROBE_REGISTRATION_SECRET", "probe-staleness-secret")
    tag = uuid.uuid4().hex[:6]
    probe = await _register_vm(test_database_url, tag)

    engine, maker = await _session(test_database_url)
    async with maker() as session:
        # 三次 15 秒心跳：健康
        for i in range(3):
            await probes_api_record_heartbeat(
                session,
                probe["probe_id"],
                observed_at=datetime.now(UTC) - timedelta(seconds=45 - i * 15),
                queue_depth=0,
                dropped=0,
                credential=probe["credential"],
            )
        await session.commit()

        status = (
            await session.execute(
                select(SourceStatus).where(
                    SourceStatus.source_type == "probe",
                    SourceStatus.source_instance == probe["probe_id"],
                )
            )
        ).scalar_one()
        assert status.state in ("healthy", "degraded")

        # 超过三个心跳周期未更新 → stale
        from apps.control_plane.integrations.status_service import mark_stale_sources

        status.updated_at = datetime.now(UTC) - timedelta(seconds=46)
        await session.commit()
        stale_count = await mark_stale_sources(session)
        assert stale_count >= 1
        await session.commit()

        refreshed = (
            await session.execute(select(SourceStatus).where(SourceStatus.id == status.id))
        ).scalar_one()
        assert refreshed.state == "stale"
    await engine.dispose()


async def probes_api_record_heartbeat(
    session, probe_id, *, observed_at, queue_depth, dropped, credential
):
    from apps.control_plane.api import probes as probes_api

    return await probes_api.heartbeat_core(
        session,
        probe_id=probe_id,
        observed_at=observed_at,
        queue_depth=queue_depth,
        dropped_count=dropped,
        oldest_queued_at=None,
        clock_offset_seconds=None,
        gaps=[],
        provided_credential=credential,
    )
