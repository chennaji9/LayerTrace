"""回放与实验场景服务（T055）。

- 回放事实经正式管线入库：库存 upsert（资源/别名/关系）→ 事件幂等写入 →
  任务投影 → 中断推断 → 事件告警 → 诊断修订；与真实数据完全同路径；
- verify 独立执行测试断言，期望断言只存于 scenario_run.verification_result，
  禁止写入任何产品表（事件/诊断/证据）；
- 重跑隔离：同一剧本可清库后重跑，结果一致；
- 并发冲突：同一剧本已有活动 run 时抛 ConflictError（API 映射 409）。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.config import get_settings
from apps.control_plane.ingestion import repository as ingestion_repository
from apps.control_plane.inventory.service import apply_discovery_batch
from apps.control_plane.models import Alert, Diagnosis, Event, Incident, ScenarioRun
from apps.control_plane.models.scenario import SCENARIO_TRANSITIONS
from packages.contracts.schemas import EventBatch, EventInput

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"

SCENARIO_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,63}$")


class ScenarioError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def _transition(run: ScenarioRun, new_state: str) -> None:
    if new_state not in SCENARIO_TRANSITIONS[run.state]:
        raise ScenarioError(
            409, "invalid_state", f"状态 {run.state} 不允许转换到 {new_state}"
        )
    run.state = new_state
    if new_state in ("succeeded", "failed", "restored", "restore_failed"):
        run.ended_at = datetime.now(UTC)


async def _pipeline(session: AsyncSession) -> None:
    """与真实数据一致的正式管线。"""
    from apps.control_plane.ingestion.pipeline import consume_events
    while await consume_events(session):
        pass


async def run_scenario(
    session: AsyncSession,
    name: str,
    *,
    mode: str,
    started_by: str,
    timeout_seconds: int | None = None,
) -> dict:
    if mode not in ("replay", "lab"):
        raise ScenarioError(400, "bad_request", "mode 必须是 replay 或 lab")
    if mode == "lab":
        raise ScenarioError(403, "real_lab_cli_required", "真实故障仅通过受限 CLI 操作；Web API 只提供回放")

    from integrations.replay.loader import ScenarioLoadError, load_scenario_by_name

    try:
        scenario = load_scenario_by_name(FIXTURES_DIR, name)
    except ScenarioLoadError as exc:
        raise ScenarioError(404, "not_found", str(exc)) from exc

    # 并发冲突：同一剧本已有活动 run
    active = (
        await session.execute(
            select(ScenarioRun).where(
                ScenarioRun.scenario_name == name,
                ScenarioRun.state.in_(["pending", "running", "verifying"]),
            )
        )
    ).scalar_one_or_none()
    if active is not None:
        raise ScenarioError(409, "conflict", f"剧本 {name} 已有活动运行 {active.id}")

    settings = get_settings()
    tenant_id = uuid.UUID(settings.tenant_id)
    project_id = uuid.UUID(settings.project_id)
    now = datetime.now(UTC)
    deadline = now + timedelta(seconds=timeout_seconds or scenario.timeout_seconds)

    run = ScenarioRun(
        scenario_name=name,
        mode=mode,
        state="running",
        target_refs=[],
        started_by=started_by,
        started_at=now,
        deadline_at=deadline,
        provenance="replayed" if mode == "replay" else "simulated",
        original_configuration={"kind": scenario.kind, "description": scenario.description},
    )
    session.add(run)
    await session.flush()

    try:
        provider_scenario = scenario
        base_time = now
        expanded = provider_scenario.expand(base_time)

        # 1) 库存：资源 + 别名 + 关系（replayed 来源）
        await apply_discovery_batch(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            batch=expanded.discovery_batch(),
            now=base_time,
        )

        # 2) 事件：经幂等写入（与真实事件同一仓储路径）
        events = [
            EventInput(
                schema_version="1.0",
                event_id=e.event_id,
                event_type=e.event_type,
                resource_ref={
                    "namespace": e.namespace,
                    "external_id": e.external_id,
                },
                occurred_at=e.occurred_at,
                observed_at=e.observed_at,
                severity=e.severity,  # type: ignore[arg-type]
                source=e.source,
                provenance="replayed",  # type: ignore[arg-type]
                correlation=(
                    {"task_id": e.correlation.get("task_id")} if e.correlation else None
                ),
                attributes=dict(e.attributes),
                scenario={"name": e.scenario_name or name, "offset": e.scenario_offset or "+0s"},
            )
            for e in expanded.events
        ]
        if events:
            await ingestion_repository.ingest_batch(
                session,
                tenant_id=tenant_id,
                project_id=project_id,
                batch=EventBatch(schema_version="1.0", events=events),
            )

        # 3) 正式管线
        _transition(run, "verifying")
        await _pipeline(session)
        _transition(run, "succeeded")
        run.verification_result = None  # verify 独立执行
    except ScenarioError:
        raise
    except Exception as exc:  # noqa: BLE001
        run.failure_reason = str(exc)[:1000]
        _transition_safe(run, "failed")
        raise ScenarioError(500, "scenario_failed", f"场景执行失败：{exc}") from exc

    await session.flush()
    return _run_dict(run)


def _transition_safe(run: ScenarioRun, new_state: str) -> None:
    try:
        _transition(run, new_state)
    except ScenarioError:
        run.state = new_state


def _run_dict(run: ScenarioRun) -> dict:
    return {
        "id": str(run.id),
        "scenario_name": run.scenario_name,
        "mode": run.mode,
        "state": run.state,
        "started_at": run.started_at,
        "deadline_at": run.deadline_at,
        "ended_at": run.ended_at,
        "verification_result": run.verification_result,
        "restore_result": run.restore_result,
    }


def _resolve_path(context: dict, path: str):
    """解析 point 路径（a.b[0].c）。"""
    current = context
    for part in re.split(r"\.", path):
        match = re.match(r"^([^\[\]]+)(?:\[(\d+)\])?$", part)
        if not match:
            return None
        key, index = match.group(1), match.group(2)
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
        if index is not None:
            try:
                current = current[int(index)]
            except (IndexError, TypeError):
                return None
    return current


def _evaluate_assertion(context: dict, assertion: dict) -> tuple[bool, str]:
    actual = _resolve_path(context, assertion["path"])
    op = assertion["operator"]
    expected = assertion.get("expected")
    if op == "exists":
        return (actual is not None) == bool(expected), f"{assertion['path']} exists={actual is not None}"
    if actual is None:
        return False, f"{assertion['path']} 未解析"
    if op == "equals":
        return actual == expected, f"{assertion['path']}={actual!r} expected={expected!r}"
    if op == "not_equals":
        return actual != expected, f"{assertion['path']}={actual!r} != {expected!r}"
    if op == "contains":
        return expected in actual, f"{assertion['path']} contains {expected!r}"
    if op == "count_equals":
        return len(actual) == expected, f"len({assertion['path']})={len(actual)} expected={expected!r}"
    if op == "less_than":
        return actual < expected, f"{assertion['path']}={actual!r} < {expected!r}"
    if op == "greater_than":
        return actual > expected, f"{assertion['path']}={actual!r} > {expected!r}"
    return False, f"未知操作符 {op}"


async def _build_verify_context(session: AsyncSession, run: ScenarioRun) -> dict:
    """从产品表构建断言上下文（只读；不把期望写入产品）。"""
    events = (
        (await session.execute(select(Event).where(Event.scenario_name == run.scenario_name)))
        .scalars()
        .all()
    )
    source_alert_ids = [f"event:{event.id}" for event in events]
    scenario_fingerprints = set(
        (
            await session.execute(
                select(Alert.fingerprint).where(
                    Alert.source == "event",
                    Alert.source_alert_id.in_(source_alert_ids),
                )
            )
        )
        .scalars()
        .all()
    ) if source_alert_ids else set()
    diagnoses = (await session.execute(select(Diagnosis))).scalars().all()
    relevant = []
    for diag in diagnoses:
        incident = (
            await session.execute(select(Incident).where(Incident.id == diag.incident_id))
        ).scalar_one_or_none()
        if incident is None:
            continue
        # Exact provenance: an incident belongs to this scenario only when one of
        # its alerts was derived from an event carrying this scenario name.
        if incident.fingerprint in scenario_fingerprints:
            relevant.append((diag, incident))

    r1_high_count = sum(
        1
        for diag, _ in relevant
        for c in diag.candidates
        if c.get("rule_id") == "R1" and c.get("support_level") == "high"
    )
    r2_high_count = sum(
        1
        for diag, _ in relevant
        for c in diag.candidates
        if c.get("rule_id") == "R2" and c.get("support_level") in ("high", "medium")
    )
    r3_count = sum(
        1 for diag, _ in relevant for c in diag.candidates if c.get("rule_id") == "R3"
    )
    r4_count = sum(
        1 for diag, _ in relevant for c in diag.candidates if c.get("rule_id") == "R4"
    )
    r4a_count = sum(
        1
        for diag, _ in relevant
        for c in diag.candidates
        if c.get("rule_id") == "R4" and "邻居" in str(c.get("title", ""))
    )
    r4b_count = sum(
        1
        for diag, _ in relevant
        for c in diag.candidates
        if c.get("rule_id") == "R4" and "R4b" in str(c.get("title", ""))
    )
    all_events_replayed = bool(events) and all(e.provenance == "replayed" for e in events)
    gpu_oom_evidence_present = any(e.type == "gpu.cuda_oom" for e in events)
    trusted_mapping_present = any(
        e.type == "gpu.device_mapping.observed"
        and (e.payload or {}).get("mapping_trusted") is True
        for e in events
    )

    diagnosis_payload = None
    contradicting_count = 0
    if relevant:
        diag, _ = max(relevant, key=lambda pair: pair[0].revision)
        diagnosis_payload = {
            "revision": diag.revision,
            "candidates": [
                {
                    "rule_id": c.get("rule_id"),
                    "support_level": c.get("support_level"),
                }
                for c in diag.candidates
            ],
        }
        contradicting_count = len(diag.contradicting_evidence_ids)

    return {
        "event_count": len(events),
        "all_events_replayed": all_events_replayed,
        "incident_count": len({str(inc.id) for _, inc in relevant}),
        "r1_high_count": r1_high_count,
        "r2_high_count": r2_high_count,
        "r3_count": r3_count,
        "r4_count": r4_count,
        "r4a_count": r4a_count,
        "r4b_count": r4b_count,
        "gpu_oom_evidence_present": gpu_oom_evidence_present,
        "trusted_mapping_present": trusted_mapping_present,
        "diagnosis": diagnosis_payload,
        "contradicting_evidence_count": contradicting_count,
    }


async def verify_scenario(session: AsyncSession, run_id: str) -> dict:
    run = (
        await session.execute(select(ScenarioRun).where(ScenarioRun.id == uuid.UUID(run_id)))
    ).scalar_one_or_none()
    if run is None:
        raise ScenarioError(404, "not_found", "场景运行不存在")

    from integrations.replay.loader import ScenarioLoadError, load_scenario_by_name

    try:
        scenario = load_scenario_by_name(FIXTURES_DIR, run.scenario_name)
    except ScenarioLoadError as exc:
        raise ScenarioError(500, "scenario_missing", str(exc)) from exc

    context = await _build_verify_context(session, run)
    results = []
    passed = True
    for assertion in scenario.expected_assertions:
        ok, detail = _evaluate_assertion(context, assertion)
        results.append({"path": assertion["path"], "operator": assertion["operator"], "passed": ok, "detail": detail})
        passed = passed and ok

    run.verification_result = {
        "passed": passed,
        "assertions": results,
        "context_summary": {
            "event_count": context["event_count"],
            "incident_count": context["incident_count"],
            "r1_high_count": context["r1_high_count"],
        },
        "verified_at": datetime.now(UTC).isoformat(),
    }
    await session.flush()
    return _run_dict(run)


async def restore_scenario(session: AsyncSession, run_id: str) -> dict:
    run = (
        await session.execute(select(ScenarioRun).where(ScenarioRun.id == uuid.UUID(run_id)))
    ).scalar_one_or_none()
    if run is None:
        raise ScenarioError(404, "not_found", "场景运行不存在")

    _transition(run, "restoring")
    # 回放模式不修改真实环境：恢复是记录性操作
    run.restore_result = {
        "mode": run.mode,
        "noop": True,
        "success": True,
        "note": "回放不修改真实环境；真实注入恢复由 scenarios/executor.py 执行",
        "restored_at": datetime.now(UTC).isoformat(),
    }
    _transition(run, "restored")
    await session.flush()
    return _run_dict(run)
