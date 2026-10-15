"""US3 集成测试：两 VM 同名容器、容器重建、历史任务不串联。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.models import Relationship, Resource, ResourceAlias, TaskRun

pytestmark = [pytest.mark.integration]

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000001")
PROJECT = uuid.UUID("00000000-0000-0000-0000-000000000001")
TAG = uuid.uuid4().hex[:6]


async def _engine_maker(test_database_url: str):
    engine = create_async_engine(test_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _insert_resource(session, *, kind, provider, source_id, display_name, now, namespace=None):
    from apps.control_plane.models import Resource as R

    resource = R(
        tenant_id=TENANT, project_id=PROJECT, kind=kind, provider=provider,
        provider_instance="default", source_id=source_id, display_name=display_name,
        provenance="observed", first_seen=now, last_seen=now,
    )
    session.add(resource)
    await session.flush()
    if namespace:
        session.add(
            ResourceAlias(
                resource_id=resource.id, namespace=namespace, external_id=source_id,
                valid_from=now, provenance="observed",
            )
        )
        await session.flush()
    return resource


async def test_two_vms_same_named_container_not_merged(test_database_url, clean_db) -> None:
    engine, maker = await _engine_maker(test_database_url)
    now = datetime.now(UTC)
    async with maker() as session:
        vm1 = await _insert_resource(session, kind="vm", provider="zsvirt", source_id=f"{TAG}-vm1", display_name="vm", now=now)
        vm2 = await _insert_resource(session, kind="vm", provider="zsvirt", source_id=f"{TAG}-vm2", display_name="vm", now=now)
        # 两台 VM 上同名容器（不同完整 ID）
        c1 = await _insert_resource(session, kind="container", provider="docker", source_id=f"{TAG}-c1" + "0" * 58, display_name="web", now=now)
        c2 = await _insert_resource(session, kind="container", provider="docker", source_id=f"{TAG}-c2" + "0" * 58, display_name="web", now=now)
        for vm, c in ((vm1, c1), (vm2, c2)):
            session.add(Relationship(
                tenant_id=TENANT, project_id=PROJECT, from_id=vm.id, type="contains",
                to_id=c.id, valid_from=now, source="test", provenance="observed",
            ))
        await session.commit()

        # 名合并检查：显示名相同的容器仍是两个资源
        rows = (await session.execute(select(Resource).where(Resource.kind == "container"))).scalars().all()
        names = [r.display_name for r in rows]
        assert names.count("web") == 2
        assert {r.id for r in rows} == {c1.id, c2.id}
    await engine.dispose()


async def test_container_rebuild_closes_old_relationship(test_database_url, clean_db) -> None:
    from apps.control_plane.inventory.relationships import close_and_rebuild

    engine, maker = await _engine_maker(test_database_url)
    t0 = datetime.now(UTC) - timedelta(minutes=10)
    t1 = datetime.now(UTC)
    async with maker() as session:
        vm = await _insert_resource(session, kind="vm", provider="zsvirt", source_id=f"{TAG}-vm3", display_name="vm3", now=t0)
        old = await _insert_resource(
            session, kind="container", provider="docker", source_id=f"{TAG}-old" + "0" * 58,
            display_name="web", now=t0, namespace="docker.container",
        )
        si_old = await _insert_resource(
            session, kind="service_instance", provider="otel", source_id=f"{TAG}-si-old",
            display_name="web 实例", now=t0, namespace="otel.service_instance",
        )
        rel = Relationship(
            tenant_id=TENANT, project_id=PROJECT, from_id=vm.id, type="contains",
            to_id=old.id, valid_from=t0, source="docker", provenance="observed",
        )
        session.add(rel)
        session.add(Relationship(
            tenant_id=TENANT, project_id=PROJECT, from_id=old.id, type="runs",
            to_id=si_old.id, valid_from=t0, source="docker", provenance="observed",
        ))
        # 旧实例上有一个历史任务
        task = TaskRun(
            tenant_id=TENANT, project_id=PROJECT, task_id=f"{TAG}-task-1",
            service_instance_id=si_old.id, started_at=t0, ended_at=t0 + timedelta(seconds=5),
            status="succeeded", status_provenance="observed",
        )
        session.add(task)
        await session.commit()

        # 容器重建：新容器 + 新服务实例出现
        new = await _insert_resource(
            session, kind="container", provider="docker", source_id=f"{TAG}-new" + "0" * 58,
            display_name="web", now=t1, namespace="docker.container",
        )
        si_new = await _insert_resource(
            session, kind="service_instance", provider="otel", source_id=f"{TAG}-si-new",
            display_name="web 实例", now=t1, namespace="otel.service_instance",
        )

        await close_and_rebuild(
            session,
            vm_id=vm.id,
            old_container=old,
            new_container=new,
            old_service_instance=si_old,
            new_service_instance=si_new,
            at=t1,
        )
        await session.commit()

        # 旧 contains 关系已关闭，新关系已建立
        rels = (await session.execute(select(Relationship).where(Relationship.type == "contains"))).scalars().all()
        old_rel = next(r for r in rels if r.to_id == old.id)
        assert old_rel.valid_to == t1
        new_rel = next(r for r in rels if r.to_id == new.id)
        assert new_rel.valid_to is None and new_rel.valid_from == t1

        # 历史任务不串联到新实例
        refreshed = (await session.execute(select(TaskRun).where(TaskRun.task_id == f"{TAG}-task-1"))).scalar_one()
        assert str(refreshed.service_instance_id) == str(si_old.id)
    await engine.dispose()


async def test_historical_topology_returns_old_instance(test_database_url, clean_db) -> None:
    from apps.control_plane.inventory.temporal_graph import effective_topology

    engine, maker = await _engine_maker(test_database_url)
    t0 = datetime.now(UTC) - timedelta(minutes=10)
    t1 = datetime.now(UTC)
    async with maker() as session:
        vm = await _insert_resource(session, kind="vm", provider="zsvirt", source_id=f"{TAG}-vm4", display_name="vm4", now=t0)
        old = await _insert_resource(session, kind="container", provider="docker", source_id=f"{TAG}-o4" + "0" * 58, display_name="web", now=t0)
        new = await _insert_resource(session, kind="container", provider="docker", source_id=f"{TAG}-n4" + "0" * 58, display_name="web", now=t1)
        session.add(Relationship(
            tenant_id=TENANT, project_id=PROJECT, from_id=vm.id, type="contains",
            to_id=old.id, valid_from=t0, valid_to=t1, source="docker", provenance="observed",
        ))
        session.add(Relationship(
            tenant_id=TENANT, project_id=PROJECT, from_id=vm.id, type="contains",
            to_id=new.id, valid_from=t1, source="docker", provenance="observed",
        ))
        await session.commit()

        at_past = t0 + timedelta(minutes=5)
        nodes, edges, _ = await effective_topology(session, PROJECT, at_past, root_id=vm.id)
        assert {str(n.id) for n in nodes if n.kind == "container"} == {str(old.id)}
        assert all(e.to_id == old.id for e in edges if e.type == "contains")

        nodes_now, edges_now, _ = await effective_topology(session, PROJECT, t1 + timedelta(seconds=1), root_id=vm.id)
        assert {str(n.id) for n in nodes_now if n.kind == "container"} == {str(new.id)}
    await engine.dispose()
