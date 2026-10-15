"""A reopened fault uses its own pre-fault baseline, with a fixed continuous anchor."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from apps.control_plane.diagnosis.context import _baseline_episode_start


def test_reopened_episode_anchor_does_not_follow_latest_firing_alert():
    first = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    second = first + timedelta(minutes=10)
    incident = SimpleNamespace(
        first_seen=first, last_seen=second + timedelta(minutes=1), reopen_count=1
    )
    alerts = [
        SimpleNamespace(starts_at=first, ends_at=first + timedelta(seconds=20)),
        SimpleNamespace(starts_at=second, ends_at=second + timedelta(seconds=20)),
        SimpleNamespace(starts_at=second + timedelta(seconds=50), ends_at=None),
    ]
    assert _baseline_episode_start(incident, alerts, stable_seconds=60) == second
    assert incident.first_seen == first


def test_unrecovered_episode_keeps_first_seen_across_an_observation_gap():
    first = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    incident = SimpleNamespace(
        first_seen=first, last_seen=first + timedelta(minutes=10), reopen_count=0
    )
    alerts = [
        SimpleNamespace(starts_at=first, ends_at=first + timedelta(seconds=20)),
        SimpleNamespace(starts_at=incident.last_seen, ends_at=None),
    ]
    assert _baseline_episode_start(incident, alerts, stable_seconds=60) == first


def test_recorded_reopen_anchor_survives_collector_gap():
    first = datetime(2026, 10, 15, 0, 0, tzinfo=UTC)
    second = first + timedelta(minutes=10)
    late = second + timedelta(minutes=10)
    incident = SimpleNamespace(first_seen=first, last_seen=late, reopen_count=1)
    alerts = [
        SimpleNamespace(starts_at=first, ends_at=first + timedelta(seconds=20), annotations={}),
        SimpleNamespace(
            starts_at=second,
            ends_at=second + timedelta(seconds=20),
            annotations={
                "incident_episode_started_at": second.isoformat(),
                "incident_reopen_count": 1,
            },
        ),
        SimpleNamespace(starts_at=late, ends_at=None, annotations={}),
    ]
    assert _baseline_episode_start(incident, alerts, stable_seconds=60) == second
