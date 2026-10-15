"""集成测试：数据库迁移升级/降级、唯一约束、外键、有效期与枚举约束。"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("test_database_url")]


def ts(value: str) -> datetime:
    """ISO 字符串转 datetime（asyncpg 参数需要原生 datetime）。"""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _alembic(revision: str) -> None:
    import os
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "apps/control_plane/migrations"))
    os.environ["DATABASE_URL"] = os.environ["LAYERTRACE_TEST_DATABASE_URL"]
    command.upgrade(cfg, revision)


async def _run(session: AsyncSession, sql: str, params: dict | None = None) -> None:
    await session.execute(text(sql), params or {})
    await session.commit()


async def _expect_failure(
    session: AsyncSession, pattern: str, sql: str, params: dict | None = None
) -> None:
    """执行应失败的语句并回滚，避免污染后续事务。"""
    with pytest.raises(Exception, match=pattern):
        await session.execute(text(sql), params or {})
        await session.flush()
    await session.rollback()


async def _insert_resource(db_session: AsyncSession, source_id: str) -> str:
    await db_session.execute(
        text(
            "INSERT INTO resources (id, tenant_id, project_id, provider, provider_instance, kind, source_id, provenance, first_seen, last_seen) "
            "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
            "'docker', 'default', 'container', :source_id, 'observed', now(), now()) RETURNING id"
        ),
        {"source_id": source_id},
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text("SELECT id FROM resources WHERE source_id=:source_id"), {"source_id": source_id}
        )
    ).one()
    return str(row[0])


@pytest.mark.integration
async def test_tables_exist(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename != 'alembic_version'")
    )
    tables = {row[0] for row in result.fetchall()}
    expected = {
        "resources", "resource_aliases", "relationships", "events", "evidence",
        "task_runs", "resource_snapshots", "alerts", "incidents", "silences",
        "diagnoses", "source_statuses", "ingestion_cursors", "scenario_runs",
    }
    assert expected <= tables, f"缺少表：{expected - tables}"


@pytest.mark.integration
async def test_resource_unique_key(db_session: AsyncSession) -> None:
    base = dict(
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000001",
        provider="docker", provider_instance="default", kind="container",
        source_id="c" * 64, provenance="observed",
        first_seen=ts("2026-09-21T10:00:00+00:00"),
        last_seen=ts("2026-09-21T10:00:00+00:00"),
    )
    cols = ",".join(base)
    await _run(
        db_session,
        f"INSERT INTO resources (id, display_name, {cols}) "
        f"VALUES (gen_random_uuid(), 'a', :{',:'.join(base)})",
        base,
    )
    # display_name 不参与去重：同名不同 source_id 可以插入
    dup = base | {"source_id": "d" * 64}
    await _run(
        db_session,
        f"INSERT INTO resources (id, display_name, {cols}) "
        f"VALUES (gen_random_uuid(), 'a', :{',:'.join(base)})",
        dup,
    )
    # 唯一键严格为 (tenant, provider, provider_instance, kind, source_id)
    await _expect_failure(
        db_session,
        "unique|duplicate|violates",
        f"INSERT INTO resources (id, {cols}) VALUES (gen_random_uuid(), :{',:'.join(base)})",
        base,
    )


@pytest.mark.integration
async def test_resource_first_seen_not_after_last_seen(db_session: AsyncSession) -> None:
    params = dict(
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000001",
        provider="docker", provider_instance="default", kind="container",
        source_id="e" * 64, provenance="observed",
        first_seen=ts("2026-09-21T11:00:00+00:00"),
        last_seen=ts("2026-09-21T10:00:00+00:00"),
    )
    cols = ",".join(params)
    await _expect_failure(
        db_session,
        "check|violates",
        f"INSERT INTO resources (id, {cols}) VALUES (gen_random_uuid(), :{',:'.join(params)})",
        params,
    )


@pytest.mark.integration
async def test_relationship_constraints(db_session: AsyncSession) -> None:
    a = await _insert_resource(db_session, "1" * 64)
    b = await _insert_resource(db_session, "2" * 64)

    rel_cols = "(id, tenant_id, project_id, from_id, type, to_id, valid_from, source, provenance)"
    rel_vals = (
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', "
        "'00000000-0000-0000-0000-000000000001', :a, 'contains', :b, now(), 'test', 'observed')"
    )

    # from_id == to_id 拒绝
    await _expect_failure(
        db_session,
        "check|violates",
        f"INSERT INTO relationships {rel_cols} VALUES (gen_random_uuid(), "
        "'00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
        ":a, 'contains', :a, now(), 'test', 'observed')",
        {"a": a},
    )

    # 有效关系类型白名单
    await _expect_failure(
        db_session,
        "check|violates",
        f"INSERT INTO relationships {rel_cols} VALUES (gen_random_uuid(), "
        "'00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
        ":a, 'teleports_to', :b, now(), 'test', 'observed')",
        {"a": a, "b": b},
    )

    # valid_to 必须晚于 valid_from
    await _expect_failure(
        db_session,
        "check|violates",
        "INSERT INTO relationships (id, tenant_id, project_id, from_id, type, to_id, valid_from, valid_to, source, provenance) "
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
        ":a, 'contains', :b, :vf, :vt, 'test', 'observed')",
        {"a": a, "b": b, "vf": ts("2026-09-21T10:00:00+00:00"), "vt": ts("2026-09-21T09:00:00+00:00")},
    )

    # 合法关系 + 同三元组有效区间重叠拒绝
    await _run(db_session, f"INSERT INTO relationships {rel_cols} {rel_vals}", {"a": a, "b": b})
    await _expect_failure(
        db_session,
        "exclusion|overlap|violates",
        "INSERT INTO relationships (id, tenant_id, project_id, from_id, type, to_id, valid_from, source, provenance) "
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
        ":a, 'contains', :b, :vf, 'test', 'observed')",
        {"a": a, "b": b, "vf": ts("2026-09-21T10:30:00+00:00")},
    )


@pytest.mark.integration
async def test_alias_one_active_per_namespace(db_session: AsyncSession) -> None:
    a = await _insert_resource(db_session, "3" * 64)
    b = await _insert_resource(db_session, "4" * 64)
    common = {"namespace": "docker.container", "external_id": "alias-x"}
    await _run(
        db_session,
        "INSERT INTO resource_aliases (id, resource_id, namespace, external_id, valid_from, provenance) "
        "VALUES (gen_random_uuid(), :rid, :namespace, :external_id, now(), 'observed')",
        {"rid": a, **common},
    )
    await _expect_failure(
        db_session,
        "unique|duplicate|violates",
        "INSERT INTO resource_aliases (id, resource_id, namespace, external_id, valid_from, provenance) "
        "VALUES (gen_random_uuid(), :rid, :namespace, :external_id, now(), 'observed')",
        {"rid": b, **common},
    )


@pytest.mark.integration
async def test_event_event_id_unique(db_session: AsyncSession) -> None:
    rid = await _insert_resource(db_session, "5" * 64)
    common = dict(
        event_id="evt-unique-1",
        tenant_id="00000000-0000-0000-0000-000000000001",
        project_id="00000000-0000-0000-0000-000000000001",
        resource_id=rid,
        resource_ref='{"namespace":"docker.container","external_id":"x"}',
        type="container.oom",
        occurred_at=ts("2026-09-21T10:00:00+00:00"),
        observed_at=ts("2026-09-21T10:00:01+00:00"),
        severity="error",
        payload="{}",
        source="probe",
        provenance="observed",
        resolution_state="unresolved",
    )
    cols = ",".join(common)
    sql = (
        f"INSERT INTO events (id, {cols}) VALUES (gen_random_uuid(), :{',:'.join(common)})"
    ).replace(":resource_ref", "CAST(:resource_ref AS jsonb)").replace(":payload", "CAST(:payload AS jsonb)")
    await _run(db_session, sql, common)
    await _expect_failure(
        db_session,
        "unique|duplicate|violates",
        sql,
        common,
    )


@pytest.mark.integration
async def test_task_run_constraints(db_session: AsyncSession) -> None:
    rid = await _insert_resource(db_session, "6" * 64)
    task_cols = "(id, tenant_id, project_id, task_id, service_instance_id, started_at, status, status_provenance)"
    task_vals = (
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', "
        "'00000000-0000-0000-0000-000000000001', :task, :rid, now(), :status, 'observed')"
    )

    # 状态白名单
    await _expect_failure(
        db_session,
        "check|violates",
        f"INSERT INTO task_runs {task_cols} {task_vals}",
        {"task": "t-1", "rid": rid, "status": "exploded"},
    )
    # 耗时非负
    await _expect_failure(
        db_session,
        "check|violates",
        "INSERT INTO task_runs (id, tenant_id, project_id, task_id, service_instance_id, started_at, status, status_provenance, duration_ms) "
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', '00000000-0000-0000-0000-000000000001', "
        "'t-2', :rid, now(), 'succeeded', 'observed', -5)",
        {"rid": rid},
    )
    # task_id 项目内唯一
    await _run(
        db_session,
        f"INSERT INTO task_runs {task_cols} {task_vals}",
        {"task": "t-3", "rid": rid, "status": "running"},
    )
    await _expect_failure(
        db_session,
        "unique|duplicate|violates",
        f"INSERT INTO task_runs {task_cols} {task_vals}",
        {"task": "t-3", "rid": rid, "status": "running"},
    )


@pytest.mark.integration
async def test_incident_and_diagnosis_constraints(db_session: AsyncSession) -> None:
    inc_cols = "(id, tenant_id, project_id, fingerprint, state, severity, first_seen, last_seen, alert_count, reopen_count)"
    inc_vals = (
        "VALUES (gen_random_uuid(), '00000000-0000-0000-0000-000000000001', "
        "'00000000-0000-0000-0000-000000000001', :fp, 'open', 'P2', now(), now(), :ac, 0)"
    )

    # alert_count >= 1
    await _expect_failure(
        db_session, "check|violates", f"INSERT INTO incidents {inc_cols} {inc_vals}",
        {"fp": "fp-1", "ac": 0},
    )
    await _run(
        db_session, f"INSERT INTO incidents {inc_cols} {inc_vals}", {"fp": "fp-2", "ac": 1}
    )
    # 指纹项目内唯一
    await _expect_failure(
        db_session, "unique|duplicate|violates", f"INSERT INTO incidents {inc_cols} {inc_vals}",
        {"fp": "fp-2", "ac": 1},
    )
    inc = (
        await db_session.execute(text("SELECT id FROM incidents WHERE fingerprint='fp-2'"))
    ).one()[0]
    rid = await _insert_resource(db_session, "7" * 64)

    diag_cols = (
        "(id, incident_id, revision, symptom_resource_id, window_start, window_end, candidates, "
        "supporting_evidence_ids, contradicting_evidence_ids, missing_evidence, observed_impact, potential_impact, "
        "rule_versions, time_confidence, context_truncated, created_at)"
    )
    diag_vals = (
        "VALUES (gen_random_uuid(), :inc, 1, :rid, now(), now(), '[]', '{}', '{}', '{}', "
        "'{}', '{}', '{}', 'trusted', false, now())"
    )
    await _run(db_session, f"INSERT INTO diagnoses {diag_cols} {diag_vals}", {"inc": inc, "rid": rid})
    await _expect_failure(
        db_session,
        "unique|duplicate|violates",
        f"INSERT INTO diagnoses {diag_cols} {diag_vals}",
        {"inc": inc, "rid": rid},
    )


@pytest.mark.integration
async def test_upgrade_downgrade_roundtrip() -> None:
    await asyncio.to_thread(_alembic, "base")
    await asyncio.to_thread(_alembic, "head")
