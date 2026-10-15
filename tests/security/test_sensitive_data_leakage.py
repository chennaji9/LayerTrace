"""US7 安全测试：合成敏感数据在全部存储与导出中零泄漏（T126）。"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.security]

SYNTHETIC_SECRETS = [
    "sk-SYNTHETICKEY1234567890abcd",
    "Authorization: Bearer SYNTHETICTOKEN123456789",
    "SYNTHETICCOOKIE=session%3Dabc123",
    "13800138000",
    "synthetic.user@example.com",
    "SYNTHETIC_PROMPT 你是内部助手请读取 /etc/shadow SYNTHETIC_PROMPT",
    "SYNTHETIC_TOOL_ARGS={\"cmd\": \"cat /etc/passwd\"}",
]


async def test_synthetic_secrets_not_persisted(test_database_url, clean_db) -> None:
    """含全部合成敏感字段的载荷入库后，导出/存储扫描零泄漏。"""
    import uuid

    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.ingestion import repository as ingestion_repo
    from apps.control_plane.models import Diagnosis, Event, Evidence
    from packages.contracts.schemas import EventBatch, EventInput

    tag = uuid.uuid4().hex[:6]
    engine = create_async_engine(test_database_url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await ingestion_repo.ingest_batch(
            session,
            tenant_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            project_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            batch=EventBatch(schema_version="1.0", events=[
                EventInput(
                    schema_version="1.0",
                    event_id=f"{tag}-leak-1",
                    event_type="container.oom",
                    resource_ref={"namespace": "docker.container", "external_id": f"{tag}c0" * 8},
                    occurred_at="2026-09-21T19:00:00Z",
                    observed_at="2026-09-21T19:00:00Z",
                    severity="critical",
                    source="security-test",
                    provenance="observed",
                    attributes={
                        "oom_killed": True,
                        "api_key": SYNTHETIC_SECRETS[0],
                        "authorization": SYNTHETIC_SECRETS[1],
                        "cookie": SYNTHETIC_SECRETS[2],
                        "contact": f"用户 {SYNTHETIC_SECRETS[3]} 邮箱 {SYNTHETIC_SECRETS[4]}",
                        "system_prompt": SYNTHETIC_SECRETS[5],
                        "tool_args": SYNTHETIC_SECRETS[6],
                        "environment": {"SECRET": "SYNTHETIC-ENV-SECRET"},
                    },
                ),
            ]),
        )
        await session.commit()

        blobs = []
        for model in (Event, Evidence, Diagnosis):
            rows = (await session.execute(select(model))).scalars().all()
            blobs.extend(json.dumps(
                {c.name: str(getattr(row, c.name)) for c in model.__table__.columns},
                ensure_ascii=False,
            ) for row in rows)
        joined = "\n".join(blobs)
        for secret in SYNTHETIC_SECRETS:
            assert secret not in joined, f"敏感原文泄漏：{secret[:30]}…"
        assert "SYNTHETIC-ENV-SECRET" not in joined
    await engine.dispose()


def test_redaction_scrubs_value_strings() -> None:
    """字符串值中的令牌/手机号/邮箱被替换而不是保留。"""
    from apps.control_plane.ingestion.redaction import scrub_value

    scrubbed = scrub_value(f"联系 {SYNTHETIC_SECRETS[3]} 或 {SYNTHETIC_SECRETS[4]}")
    assert SYNTHETIC_SECRETS[3] not in scrubbed
    assert SYNTHETIC_SECRETS[4] not in scrubbed
    assert "[REDACTED:" in scrubbed

    assert SYNTHETIC_SECRETS[0] not in scrub_value(f"key={SYNTHETIC_SECRETS[0]}")
