"""US6 集成测试：失败/超时自动恢复、原配置恢复与 restore_failed 可见性。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.control_plane.models import ScenarioRun

pytestmark = [pytest.mark.integration]


async def _session(test_database_url: str):
    engine = create_async_engine(test_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_deadline_auto_restore(test_database_url, clean_db) -> None:
    from apps.control_plane.scenarios.executor import enforce_deadlines

    engine, maker = await _session(test_database_url)
    async with maker() as session:
        run = ScenarioRun(
            scenario_name="a1-container-oom", mode="lab", state="running",
            target_refs=[], started_by="test",
            started_at=datetime.now(UTC) - timedelta(minutes=5),
            deadline_at=datetime.now(UTC) - timedelta(minutes=1),  # 已超时
            provenance="simulated",
            original_configuration={"snapshots": {"c1": {"memory_limit_bytes": 536870912}},
                                    "restore_actions": [{"operation": "restore_snapshot", "target_ref": "c1"}]},
        )
        session.add(run)
        await session.commit()

        restored = await enforce_deadlines(session)
        assert restored >= 1

        refreshed = (
            await session.execute(select(ScenarioRun).where(ScenarioRun.id == run.id))
        ).scalar_one()
        assert refreshed.state == "restored"
        assert refreshed.restore_result["success"] is True
        assert refreshed.restore_result["deadline_triggered"] is True
        assert refreshed.ended_at is not None
    await engine.dispose()


async def test_restore_failed_visible(test_database_url, clean_db) -> None:
    from apps.control_plane.scenarios.executor import InjectionPort, restore_run

    class FailingPort(InjectionPort):
        async def apply(self, action: dict, target_ref: str) -> dict:
            raise RuntimeError("恢复操作失败（模拟）")

    engine, maker = await _session(test_database_url)
    async with maker() as session:
        run = ScenarioRun(
            scenario_name="b-cpu-quota", mode="lab", state="restoring",
            target_refs=[], started_by="test",
            started_at=datetime.now(UTC) - timedelta(minutes=3),
            deadline_at=datetime.now(UTC) + timedelta(minutes=10),
            provenance="simulated",
            original_configuration={"restore_actions": [{"operation": "restore_snapshot", "target_ref": "c1"}]},
        )
        session.add(run)
        await session.flush()

        result = await restore_run(session, run, port=FailingPort())
        assert result["success"] is False
        assert "恢复操作失败" in result["error"]
        await session.commit()
    await engine.dispose()
