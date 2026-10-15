"""单 worker：平台轮询、告警轮询、诊断任务与超时调度。

- 全部轮询/调度只在本进程执行；Web 进程（main.py）永不导入本模块的作业，
  防止多个 Web worker 重复执行轮询。
- 每个作业有固定间隔、失败指数退避 + 抖动，成功后重置。
- 依赖的服务模块按阶段交付；未就绪的作业记录一次警告后空转，不抛错终止。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from apps.control_plane.config import get_settings

logger = logging.getLogger("layertrace.worker")


@dataclass
class Job:
    name: str
    interval: float
    func: Callable[[], Awaitable[None]]
    backoff_base: float = 1.5
    max_backoff: float = 300.0
    failures: int = 0
    unavailable: bool = False
    last_run: float = 0.0
    stats: dict[str, float] = field(default_factory=lambda: {"ok": 0.0, "error": 0.0})


def _next_delay(job: Job) -> float:
    """正常间隔；失败时指数退避 + 全抖动。"""
    if job.failures == 0:
        return job.interval
    cap = min(job.max_backoff, job.interval * (job.backoff_base**job.failures))
    return random.uniform(0, cap)


async def _run_job_forever(job: Job, stop: asyncio.Event) -> None:
    # 启动抖动，避免多个作业同拍执行
    await asyncio.sleep(random.uniform(0, min(2.0, job.interval)))
    while not stop.is_set():
        if job.unavailable:
            await asyncio.sleep(60.0)
            continue
        started = time.monotonic()
        try:
            await job.func()
        except (NotImplementedError, ModuleNotFoundError, ImportError):
            job.unavailable = True
            logger.warning("作业 %s 依赖尚未就绪，暂时空转", job.name)
        except asyncio.CancelledError:
            raise
        except Exception:
            job.failures += 1
            job.stats["error"] += 1
            logger.exception("作业 %s 第 %d 次失败", job.name, job.failures)
        else:
            if job.failures:
                logger.info("作业 %s 恢复（此前失败 %d 次）", job.name, job.failures)
            job.failures = 0
            job.stats["ok"] += 1
        finally:
            job.last_run = time.monotonic() - started
        delay = _next_delay(job)
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            pass


# ---------------------------------------------------------------- 作业实现


async def job_alert_poll() -> None:
    """每 15 秒读取 Prometheus 告警并幂等写入（T099 交付具体实现）。"""
    mod = importlib.import_module("apps.control_plane.incidents.prometheus_poller")
    from apps.control_plane.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        await mod.poll_prometheus_alerts(session)
        await session.commit()


async def job_platform_poll() -> None:
    """平台（ZSvirt）增量轮询（T093 交付具体实现）。"""
    settings = get_settings()
    if not settings.zsvirt_enabled:
        return
    mod = importlib.import_module("apps.control_plane.integrations.platform_sync")
    from apps.control_plane.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        await mod.sync_platform(session)
        await session.commit()


async def job_diagnosis_sweep() -> None:
    """为有新证据的事件单生成诊断修订（T045 交付具体实现）。"""
    mod = importlib.import_module("apps.control_plane.ingestion.pipeline")
    from apps.control_plane.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        await mod.consume_events(session)
        await session.commit()


async def job_scenario_deadline() -> None:
    """到达 deadline 的场景必须自动恢复（T115 交付具体实现）。"""
    mod = importlib.import_module("apps.control_plane.scenarios.executor")
    from apps.control_plane.db import get_sessionmaker

    async with get_sessionmaker()() as session:
        await mod.enforce_deadlines(session)
        await session.commit()


async def job_staleness_sweep() -> None:
    """采集状态陈旧标记：超过 3 个心跳周期未更新至少进入 stale（T084 使用）。"""
    from sqlalchemy import update

    from apps.control_plane.db import get_sessionmaker
    from apps.control_plane.models import SourceStatus

    settings = get_settings()
    threshold_seconds = settings.heartbeat_interval_seconds * settings.stale_after_missed_heartbeats
    async with get_sessionmaker()() as session:
        cutoff = time.time() - threshold_seconds
        result = await session.execute(
            update(SourceStatus)
            .where(
                SourceStatus.state.in_(["healthy", "degraded"]),
                SourceStatus.updated_at
                < __import__("datetime").datetime.fromtimestamp(cutoff, __import__("datetime").UTC),
            )
            .values(state="stale")
            .returning(SourceStatus.id)
        )
        updated_ids = result.scalars().all()
        if updated_ids:
            logger.info("已将 %d 个数据源标记为 stale", len(updated_ids))
        await session.commit()


async def job_retention_sweep() -> None:
    """Expire source references and remove facts beyond configured retention."""
    from apps.control_plane.db import get_sessionmaker
    from apps.control_plane.retention import enforce_retention

    async with get_sessionmaker()() as session:
        result = await enforce_retention(session)
        await session.commit()
        if result.expired_evidence or result.deleted_events or result.deleted_diagnoses:
            logger.info("保留期清理结果 %s", result.as_dict())


def build_jobs() -> list[Job]:
    settings = get_settings()
    return [
        Job(name="alert_poll", interval=float(settings.alert_poll_seconds), func=job_alert_poll),
        Job(name="platform_poll", interval=30.0, func=job_platform_poll),
        Job(name="diagnosis_sweep", interval=5.0, func=job_diagnosis_sweep),
        Job(name="scenario_deadline", interval=5.0, func=job_scenario_deadline),
        Job(
            name="staleness_sweep",
            interval=float(settings.heartbeat_interval_seconds),
            func=job_staleness_sweep,
        ),
        Job(name="retention_sweep", interval=3600.0, func=job_retention_sweep),
    ]


async def run() -> None:
    settings = get_settings()
    logger.info("worker 启动 mode=%s instance=%s", settings.mode, settings.instance_name)
    stop = asyncio.Event()
    jobs = build_jobs()
    tasks = [asyncio.create_task(_run_job_forever(job, stop), name=job.name) for job in jobs]
    try:
        await asyncio.Event().wait()  # 由取消触发退出
    except asyncio.CancelledError:
        logger.info("worker 收到取消，停止全部作业")
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("worker 已停止")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()
