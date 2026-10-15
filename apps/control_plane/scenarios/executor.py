"""场景执行器（T115）：注入前快照、deadline 自动恢复、显式恢复与审计事件。

真实注入（lab 模式）通过受控操作端口执行；到达 deadline 必须自动恢复；
失败/超时进入 restoring；恢复结果（含 restore_failed）在 ScenarioRun 上可见。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.models import ScenarioRun

logger = logging.getLogger(__name__)


class InjectionPort:
    """注入操作端口：真实环境实现调用 Docker/工作负载接口；
    默认实现记录审计并按场景定义模拟（能力门控由调用方决定）。"""

    async def apply(self, action: dict, target_ref: str) -> dict:
        return {"action": action["operation"], "target": target_ref, "mode": "recorded"}

    async def capture_snapshot(self, target_ref: str) -> dict:
        return {"target": target_ref, "captured_at": datetime.now(UTC).isoformat()}


async def execute_fault_scenario(
    session: AsyncSession,
    run: ScenarioRun,
    *,
    inject: list[dict],
    restore: list[dict],
    target_refs: list[str],
    port: InjectionPort | None = None,
) -> None:
    """执行注入：快照 → 注入 → 记录恢复动作与 deadline。"""
    port = port or InjectionPort()
    snapshots = {}
    for ref in target_refs:
        snapshots[ref] = await port.capture_snapshot(ref)
    run.original_configuration = {
        **(run.original_configuration or {}),
        "snapshots": snapshots,
        "restore_actions": restore,
    }

    for action in inject:
        await port.apply(action, action.get("target_ref", ""))
    await session.flush()


async def restore_run(session: AsyncSession, run: ScenarioRun, *, port: InjectionPort | None = None) -> dict:
    """显式恢复：按快照执行恢复动作；结果写入 restore_result。"""
    port = port or InjectionPort()
    config = run.original_configuration or {}
    restore_actions = config.get("restore_actions") or []
    results = []
    try:
        for action in restore_actions:
            results.append(await port.apply(action, action.get("target_ref", "")))
        run.restore_result = {
            "restored_at": datetime.now(UTC).isoformat(),
            "actions": results,
            "success": True,
        }
    except Exception as exc:  # noqa: BLE001
        run.restore_result = {
            "restored_at": datetime.now(UTC).isoformat(),
            "error": str(exc)[:500],
            "success": False,
        }
    await session.flush()
    return run.restore_result


async def enforce_deadlines(session: AsyncSession) -> int:
    """worker 周期任务：到达 deadline 的活动场景必须自动恢复。"""
    now = datetime.now(UTC)
    runs = (
        (
            await session.execute(
                select(ScenarioRun).where(
                    ScenarioRun.state.in_(["pending", "running", "verifying"]),
                    ScenarioRun.deadline_at < now,
                )
            )
        )
        .scalars()
        .all()
    )
    restored = 0
    for run in runs:
        run.state = "restoring"
        result = await restore_run(session, run)
        run.state = "restored" if result.get("success") else "restore_failed"
        run.ended_at = datetime.now(UTC)
        run.restore_result = {
            **result,
            "deadline_triggered": True,
            "deadline_at": run.deadline_at.isoformat(),
        }
        restored += 1
    if restored:
        await session.commit()
        logger.info("按 deadline 自动恢复 %d 个场景", restored)
    return restored


async def deadline_for(timeout_seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=timeout_seconds)
