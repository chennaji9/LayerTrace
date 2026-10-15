"""Capture deterministic evidence for the 3-5 minute demo script (T147)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from tests.e2e.conftest import E2E_TOKEN, run_async

pytestmark = [pytest.mark.e2e]

AUTH = {"Authorization": f"Bearer {E2E_TOKEN}"}
SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "docs" / "screenshots"


def _api(base: str, method: str, path: str, **kwargs) -> httpx.Response:
    response = httpx.request(method, f"{base}{path}", headers=AUTH, timeout=60, **kwargs)
    assert response.status_code < 400, f"{path}: {response.status_code} {response.text[:300]}"
    return response


def _run_task_projection(database_url: str) -> None:
    async def _do() -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.control_plane.tasks.projection import apply_task_events

        engine = create_async_engine(database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await apply_task_events(session)
            await session.commit()
        await engine.dispose()

    run_async(_do())


def _recover_incidents(database_url: str) -> None:
    async def _do() -> None:
        from sqlalchemy import select, update
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.control_plane.incidents.recovery import evaluate_recovery
        from apps.control_plane.models import Alert, Incident

        engine = create_async_engine(database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            incidents = (await session.execute(select(Incident))).scalars().all()
            for incident in incidents:
                await session.execute(
                    update(Alert)
                    .where(Alert.fingerprint == incident.fingerprint)
                    .values(
                        state="resolved",
                        ends_at=datetime.now(UTC) - timedelta(seconds=120),
                    )
                )
                await evaluate_recovery(session, incident, now=datetime.now(UTC))
            await session.commit()
        await engine.dispose()

    run_async(_do())


def _capture(page, base: str, path: str, filename: str, selector: str) -> None:
    page.goto(f"{base}{path}", wait_until="domcontentloaded")
    page.wait_for_selector(selector, timeout=20000)
    page.wait_for_timeout(400)
    page.screenshot(path=str(SCREENSHOT_DIR / filename), full_page=True)


def test_capture_demo_flow(base_url, test_database_url, clean_db, desktop_page) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    run = _api(
        base_url,
        "POST",
        "/api/v1/scenarios/story-oom:run",
        json={"mode": "replay"},
    ).json()
    assert run["state"] == "succeeded"

    now = datetime.now(UTC)
    _api(
        base_url,
        "POST",
        "/api/v1/events:batch",
        json={
            "schema_version": "1.0",
            "events": [
                {
                    "schema_version": "1.0",
                    "event_id": "demo-normal-task-start",
                    "event_type": "task.started",
                    "resource_ref": {
                        "namespace": "otel.service_instance",
                        "external_id": "story-oom-si-tool",
                    },
                    "occurred_at": now.isoformat(),
                    "observed_at": now.isoformat(),
                    "source": "demo-agent",
                    "provenance": "observed",
                    "correlation": {"task_id": "demo-normal-task"},
                    "attributes": {"phase": "tool"},
                },
                {
                    "schema_version": "1.0",
                    "event_id": "demo-normal-task-done",
                    "event_type": "task.succeeded",
                    "resource_ref": {
                        "namespace": "otel.service_instance",
                        "external_id": "story-oom-si-tool",
                    },
                    "occurred_at": (now + timedelta(seconds=1)).isoformat(),
                    "observed_at": (now + timedelta(seconds=1)).isoformat(),
                    "source": "demo-agent",
                    "provenance": "observed",
                    "correlation": {"task_id": "demo-normal-task"},
                    "attributes": {
                        "duration_ms": 980,
                        "model_duration_ms": 420,
                        "tool_duration_ms": 310,
                    },
                },
            ],
        },
    )
    _run_task_projection(test_database_url)

    _capture(desktop_page, base_url, "/", "demo-01-overview.png", "#overview-incidents table")
    _capture(
        desktop_page,
        base_url,
        "/pages/topology",
        "demo-02-topology.png",
        "#topo-cy",
    )
    _capture(
        desktop_page,
        base_url,
        "/pages/tasks",
        "demo-03-tasks.png",
        "#task-table tbody tr",
    )
    _capture(
        desktop_page,
        base_url,
        "/pages/incidents",
        "demo-04-incidents.png",
        "#noise-panel table",
    )

    desktop_page.goto(f"{base_url}/pages/diagnosis", wait_until="domcontentloaded")
    desktop_page.wait_for_selector("#incident-table button", timeout=20000)
    desktop_page.locator("#incident-table button").first.click()
    desktop_page.wait_for_selector("#d-candidates .candidate", timeout=20000)
    desktop_page.screenshot(
        path=str(SCREENSHOT_DIR / "demo-05-diagnosis.png"), full_page=True
    )

    incident = _api(base_url, "GET", "/api/v1/incidents?state=open").json()["items"][0]
    _api(
        base_url,
        "POST",
        f"/api/v1/incidents/{incident['id']}:acknowledge",
        json={"actor": "demo-ops", "note": "演示确认"},
    )
    _api(
        base_url,
        "POST",
        "/api/v1/silences",
        json={
            "matcher": {"fingerprint": incident["fingerprint"]},
            "reason": "演示维护窗口",
            "created_by": "demo-ops",
            "starts_at": now.isoformat(),
            "ends_at": (now + timedelta(hours=1)).isoformat(),
        },
    )
    _capture(
        desktop_page,
        base_url,
        "/pages/incidents",
        "demo-06-ack-silence.png",
        "#noise-panel table",
    )

    _recover_incidents(test_database_url)
    _capture(
        desktop_page,
        base_url,
        "/pages/incidents",
        "demo-07-recovered.png",
        "#noise-panel table",
    )
    _capture(
        desktop_page,
        base_url,
        "/pages/status",
        "demo-08-status.png",
        "#status-table tbody tr",
    )
