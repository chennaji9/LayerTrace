"""Browser workflows without pre-injected Authorization headers."""

from __future__ import annotations

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN

pytestmark = [pytest.mark.e2e]
AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}


def _create_replay(base):
    response = httpx.post(
        f"{base}/api/v1/scenarios/story-oom:run", headers=AUTH,
        json={"mode": "replay"}, timeout=60,
    )
    assert response.status_code == 202, response.text
    incidents = httpx.get(f"{base}/api/v1/incidents", headers=AUTH, timeout=30).json()
    return incidents["items"][0]


def _login(page, base, path):
    page.goto(base + path)
    page.wait_for_url("**/login?next=**")
    page.fill("#access-token", E2E_TOKEN)
    page.click('#login-form button[type="submit"]')
    page.wait_for_url(base + path)


def test_login_incident_deep_link_evidence_and_silence(browser, live_server, clean_db):
    incident = _create_replay(live_server)
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    try:
        _login(page, live_server, "/pages/incidents")
        link = page.locator(f'a[href="/pages/diagnosis?incident_id={incident["id"]}"]')
        link.click()
        page.wait_for_selector(".candidate", timeout=15000)
        assert incident["id"][:8] in page.inner_text("#d-heading")
        page.locator("#d-candidates details.evidence.supporting").first.locator("summary").click()
        assert "OOM" in page.inner_text("#d-candidates")
        assert "回放" in page.inner_text("#d-candidates")
        page.goto(live_server + "/pages/incidents")
        page.locator('button[data-ack]').first.click()
        page.wait_for_function("document.querySelector('#incident-table').innerText.includes('已确认')")
        page.locator('button[data-silence]').first.click()
        assert page.input_value("#silence-fingerprint") == incident["fingerprint"]
        page.fill("#silence-reason", "浏览器验收维护窗口")
        page.click('#silence-form button[type="submit"]')
        page.wait_for_function("document.querySelector('#silence-result').innerText.includes('通知静默已创建')")
        assert page.is_visible("#logout-button")
        page.click("#logout-button")
        page.wait_for_url("**/login")
    finally:
        context.close()


def test_failed_task_opens_corresponding_diagnosis_without_headers(browser, live_server, clean_db):
    incident = _create_replay(live_server)
    detail = httpx.get(f"{live_server}/api/v1/incidents/{incident['id']}", headers=AUTH, timeout=30).json()
    task_id = detail["diagnosis"]["observed_impact"]["task_ids"][0]
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    try:
        path = "/pages/tasks?task_id=" + task_id
        _login(page, live_server, path)
        page.wait_for_selector("#task-detail h2", timeout=15000)
        assert task_id in page.inner_text("#task-detail")
        link = page.locator(f'#task-detail a[href="/pages/diagnosis?incident_id={incident["id"]}"]')
        assert link.is_visible()
        link.click()
        page.wait_for_selector(".candidate", timeout=15000)
        assert incident["id"][:8] in page.inner_text("#d-heading")
    finally:
        context.close()
