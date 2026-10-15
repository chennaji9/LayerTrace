"""历史诊断须与当前状态分开，旧引用经真实证据 API 读取并保留 URL 轮次。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN, run_async
from tests.seed_utils import PROJECT, TENANT

pytestmark = [pytest.mark.e2e]
AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


def _seed_revisions(database_url):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from apps.control_plane.models import Diagnosis, Evidence, Incident
    from tests.seed_utils import seed_full_stack

    async def seed():
        engine = create_async_engine(database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        async with maker() as session:
            stack = await seed_full_stack(session, prefix="history-e2e", now=at)
            incident = Incident(
                tenant_id=TENANT, project_id=PROJECT, fingerprint="history-e2e-cpu",
                state="resolved", severity="P2", first_seen=at, last_seen=at + timedelta(minutes=4),
                resolved_at=at + timedelta(minutes=5), alert_count=1,
            )
            evidence = Evidence(
                tenant_id=TENANT, project_id=PROJECT, kind="event",
                resource_id=stack["container"].id, range_start=at, range_end=at,
                source_ref="history-only-cpu-evidence",
                summary={"cpu_quota_us": 30000, "marker": "history-only-evidence"},
                query_parameters={"event_type": "container.cpu.sample"}, provenance="observed",
            )
            session.add_all([incident, evidence])
            await session.flush()
            for revision, support in ((1, "high"), (2, "low")):
                supporting = [str(evidence.id)] if revision == 1 else []
                session.add(Diagnosis(
                    incident_id=incident.id, revision=revision,
                    symptom_resource_id=stack["service_instance"].id,
                    window_start=at, window_end=at + timedelta(minutes=4),
                    candidates=[{
                        "rule_id": "R2", "title": "模型容器 CPU 配额瓶颈", "support_level": support,
                        "supporting": supporting, "contradicting": [],
                        "missing": [] if revision == 1 else ["本轮窗口缺少直接证据"],
                        "recommendations": ["核对配额"], "verification": ["新任务成功"],
                    }],
                    supporting_evidence_ids=[evidence.id] if revision == 1 else [],
                    contradicting_evidence_ids=[], missing_evidence=[],
                    observed_impact={}, potential_impact={}, rule_versions={"R2": "1.0"},
                    time_confidence="trusted", created_at=at + timedelta(minutes=revision),
                ))
            await session.commit()
            identities = {"incident": str(incident.id), "evidence": str(evidence.id),
                          "resource": str(evidence.resource_id)}
        await engine.dispose()
        return identities

    return run_async(seed())


def test_latest_low_to_history_high_evidence_and_refresh(browser, live_server, clean_db, test_database_url):
    identities = _seed_revisions(test_database_url)
    incident_id = identities["incident"]
    endpoint = f"/api/v1/incidents/{incident_id}"
    detail = httpx.get(live_server + endpoint, headers=AUTH, timeout=30).json()
    assert detail["diagnosis"]["revision"] == 2
    assert detail["evidence"] == [], "旧证据不能借最新详情缓存通过测试"
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    requested = []
    page.on("request", lambda request: requested.append(request.url))
    try:
        page.goto(live_server + "/pages/diagnosis?incident_id=" + incident_id)
        page.wait_for_url("**/login?next=**")
        page.fill("#access-token", E2E_TOKEN)
        page.click('#login-form button[type="submit"]')
        page.wait_for_selector('#d-candidates[data-revision="2"] .support-low')
        assert page.input_value("#diagnosis-revision") == "latest"
        page.select_option("#diagnosis-revision", "1")
        page.wait_for_selector('#d-candidates[data-revision="1"] .support-high')
        assert "revision=1" in page.url
        assert "历史诊断记录 1" in page.inner_text("#diagnosis-record-notice")
        assert "当前状态" in page.inner_text("#d-heading")
        assert "已恢复" in page.inner_text("#d-heading")
        card = page.locator(f'#d-candidates details[data-evidence-id="{identities["evidence"]}"]')
        card.locator("summary").click()
        assert "history-only-evidence" in card.inner_text()
        resource_link = card.locator('a[href*="/pages/topology?"][href*="at="]')
        assert identities["resource"] in resource_link.get_attribute("href")
        assert any(f"/api/v1/evidence/{identities['evidence']}" in url for url in requested)
        page.reload()
        page.wait_for_selector('#d-candidates[data-revision="1"] .support-high')
        assert page.input_value("#diagnosis-revision") == "1"
        assert "revision=1" in page.url
        card.locator("summary").click()
        assert "history-only-evidence" in card.inner_text()
        selector_before_refresh = page.query_selector("#diagnosis-revision")
        page.click("#refresh-button")
        page.wait_for_function("node => !node.isConnected", arg=selector_before_refresh)
        page.wait_for_selector('#d-candidates[data-revision="1"] .support-high')
        assert page.input_value("#diagnosis-revision") == "1"
        page.select_option("#diagnosis-revision", "latest")
        page.wait_for_selector('#d-candidates[data-revision="2"] .support-low')
        assert "revision=" not in page.url
        assert page.locator("#d-candidates details.evidence").count() == 0
        page.goto(live_server + f"/pages/diagnosis?incident_id={incident_id}&revision=999")
        page.wait_for_selector("#diagnosis-latest")
        assert "诊断记录 999 不存在" in page.inner_text("#d-heading")
        assert page.locator("#d-candidates .candidate").count() == 0
        page.click("#diagnosis-latest")
        page.wait_for_selector('#d-candidates[data-revision="2"] .support-low')
        assert "revision=" not in page.url
    finally:
        context.close()
