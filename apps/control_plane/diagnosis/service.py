"""诊断服务（T045）。

- 候选排序：high > medium > low，同级保持稳定顺序（分数仅用于同级排序）；
- 证据先持久化（幂等 source_ref），诊断引用证据 ID；
- 生成不可变 revision（同一事件单单调递增）；
- 观测影响与潜在影响分开：观测 = 窗口内已确认失败任务/受影响容器；
  潜在 = 同 VM 其他运行中任务与兄弟容器；
- 证据不足时允许并列候选与"证据不足"结论（candidates 可为空 + missing_evidence）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.control_plane.diagnosis.association import affected_tasks
from apps.control_plane.diagnosis.context import build_context
from apps.control_plane.diagnosis.evidence import (
    persist_event_evidence,
    persist_snapshot_evidence,
)
from apps.control_plane.diagnosis.rule_loader import load_rules
from apps.control_plane.integrations.protocols import CandidateEvaluation, DiagnosticContext
from apps.control_plane.models import Diagnosis, Incident

logger = logging.getLogger(__name__)

RULES_DIR = Path(__file__).resolve().parents[3] / "rules"

_SUPPORT_ORDER = {"high": 0, "medium": 1, "low": 2}


def _candidate_to_dict(candidate: CandidateEvaluation) -> dict:
    return {
        "rule_id": candidate.rule_id,
        "title": candidate.title,
        "support_level": candidate.support_level,
        "score": candidate.score,
        "supporting": candidate.supporting,
        "contradicting": candidate.contradicting,
        "missing": [m for m in candidate.missing if m],
        "recommendations": candidate.recommendations,
        "verification": candidate.verification,
        "detail": candidate.detail,
    }


def _collect_evidence_ids(candidates: list[CandidateEvaluation]) -> set[str]:
    ids: set[str] = set()
    for candidate in candidates:
        ids.update(candidate.supporting)
        ids.update(candidate.contradicting)
    return ids


def _impacts(
    context: DiagnosticContext, candidates: list[CandidateEvaluation]
) -> tuple[dict, dict]:
    failed = [
        t
        for t in affected_tasks(context)
        if t.get("status") in ("failed", "interrupted", "timed_out")
    ]
    observed = {
        "failed_task_count": len(failed),
        "task_ids": [t["task_id"] for t in failed],
        "symptom_resource_id": context.symptom_resource_id,
        "incident_id": context.incident_id,
    }
    vm_nodes = [n for n in context.topology_nodes if n.get("kind") == "vm"]
    other_tasks = [t for t in context.tasks if t.get("status") == "running"]
    potential = {
        "running_task_count_on_topology": len(other_tasks),
        "running_task_ids": [t["task_id"] for t in other_tasks],
        "vm_ids": [n["id"] for n in vm_nodes],
        "note": "若根因为资源层压力，同 VM 任务与兄弟容器可能受同样影响",
    }
    return observed, potential


def input_signature(context: DiagnosticContext, rules) -> str:
    content = {
        "window": context.window,
        "events": context.events,
        "tasks": context.tasks,
        "snapshots": context.snapshots,
        "edges": context.topology_edges,
        "rules": {rule.rule_id: rule.version for rule in rules},
        "baseline": context.baseline,
        "time_confidence": context.time_confidence,
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


async def generate_revision(
    session: AsyncSession, incident: Incident, *, context=None
) -> Diagnosis | None:
    context = context or await build_context(session, incident)

    rules = load_rules(RULES_DIR)
    evaluations: list[CandidateEvaluation] = []
    rule_versions: dict[str, str] = {}
    missing_all: list[str] = []

    for rule in rules:
        result = rule.evaluator.evaluate(context)
        rule_versions[rule.rule_id] = rule.version
        if result.applicable:
            evaluations.append(result)
        evaluations.extend(result.additional_candidates)
        if not result.applicable:
            missing_all.extend(f"[{rule.rule_id}] {m}" for m in result.missing if m)
        else:
            missing_all.extend(m for m in result.missing if m)

    # 证据持久化：引用到的事件与快照
    evidence_ids: set[str] = set()
    cited = _collect_evidence_ids(evaluations)
    for event in context.events:
        if event["id"] in cited:
            row = await persist_event_evidence(
                session,
                tenant_id=incident.tenant_id,
                project_id=incident.project_id,
                event=event,
                resource_id=event.get("resource_id"),
            )
            evidence_ids.add(str(row.id))
    for snapshot in context.snapshots:
        if snapshot["id"] in cited:
            row = await persist_snapshot_evidence(
                session,
                tenant_id=incident.tenant_id,
                project_id=incident.project_id,
                snapshot=snapshot,
            )
            if row:
                evidence_ids.add(str(row.id))

    # 排序：支持等级优先，同级按 rule_id 稳定排序
    evaluations.sort(key=lambda c: (_SUPPORT_ORDER.get(c.support_level, 9), c.rule_id))
    candidate_dicts = [_candidate_to_dict(c) for c in evaluations]

    supporting_ids: list[str] = []
    contradicting_ids: list[str] = []
    for candidate in evaluations:
        supporting_ids.extend(candidate.supporting)
        contradicting_ids.extend(candidate.contradicting)
    last = (
        await session.execute(
            select(Diagnosis.revision)
            .where(Diagnosis.incident_id == incident.id)
            .order_by(Diagnosis.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    revision = (last or 0) + 1

    observed_impact, potential_impact = _impacts(context, evaluations)

    if not candidate_dicts:
        missing_all.append("现有证据不足以支撑任何候选根因")

    # 支持与反证数组必填（可为空但不能缺失）；只保留已持久化为证据行的引用
    diagnosis = Diagnosis(
        incident_id=incident.id,
        revision=revision,
        symptom_resource_id=(
            uuid.UUID(context.symptom_resource_id) if context.symptom_resource_id else None
        ),
        window_start=context.window[0],
        window_end=context.window[1],
        candidates=candidate_dicts,
        supporting_evidence_ids=[uuid.UUID(e) for e in sorted(set(supporting_ids) & evidence_ids)],
        contradicting_evidence_ids=[
            uuid.UUID(e) for e in sorted(set(contradicting_ids) & evidence_ids)
        ],
        missing_evidence=sorted({m for m in missing_all if m})[:32],
        observed_impact=observed_impact,
        potential_impact=potential_impact,
        rule_versions=rule_versions,
        time_confidence=context.time_confidence,
        context_truncated=context.context_truncated,
        input_signature=input_signature(context, rules),
        summary=(
            f"{len(candidate_dicts)} 个候选；最高支持 "
            f"{candidate_dicts[0]['support_level'] if candidate_dicts else '无'}"
        ),
    )
    session.add(diagnosis)
    await session.flush()
    return diagnosis


async def sweep_pending_diagnoses(session: AsyncSession) -> int:
    """为有新证据的事件单生成诊断修订。"""
    incidents = (await session.execute(select(Incident))).scalars().all()
    generated = 0
    for incident in incidents:
        latest_signature = (
            await session.execute(
                select(Diagnosis.input_signature)
                .where(Diagnosis.incident_id == incident.id)
                .order_by(Diagnosis.revision.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        try:
            context = await build_context(session, incident)
            rules = load_rules(RULES_DIR)
            if latest_signature == input_signature(context, rules):
                continue
            await generate_revision(session, incident, context=context)
            generated += 1
        except Exception:
            logger.exception("事件单 %s 诊断生成失败", incident.id)
    if generated:
        await session.flush()
    return generated
