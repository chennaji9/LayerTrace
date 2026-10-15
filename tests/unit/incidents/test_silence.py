"""US5 单元测试：静默 matcher、起止时间、只抑制通知和到期。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from packages.contracts.schemas import SilenceRequest

pytestmark = [pytest.mark.unit]

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def _request(matcher: dict) -> SilenceRequest:
    return SilenceRequest(
        matcher=matcher,
        reason="维护窗口",
        created_by="ops@example",
        starts_at=NOW - timedelta(minutes=5),
        ends_at=NOW + timedelta(minutes=55),
    )


def test_matcher_only_fingerprint() -> None:
    req = _request({"fingerprint": "abc123"})
    assert req.matcher.fingerprint == "abc123"


def test_matcher_field_combination() -> None:
    req = _request({"rule_id": "R1", "resource_id": "11111111-1111-1111-1111-111111111111", "severity": "P2"})
    assert req.matcher.rule_id == "R1"


def test_matcher_invalid_combination_rejected() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _request({"task_id": "t-1"})  # 不允许的字段（additionalProperties=forbid）


def test_matcher_empty_rejected() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        _request({})


def test_window_inverted_rejected() -> None:
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        SilenceRequest(
            matcher={"fingerprint": "abc"},
            reason="r",
            created_by="ops",
            starts_at=NOW,
            ends_at=NOW - timedelta(minutes=1),
        )


def test_silence_matches_only_notifications() -> None:
    from apps.control_plane.incidents.silence_service import matches

    silence = {"matcher": {"fingerprint": "fp-1"}, "starts_at": (NOW - timedelta(minutes=1)).isoformat(), "ends_at": (NOW + timedelta(minutes=1)).isoformat()}
    assert matches(silence, fingerprint="fp-1", now=NOW) is True
    assert matches(silence, fingerprint="fp-2", now=NOW) is False
    # 到期后不再命中
    assert matches(silence, fingerprint="fp-1", now=NOW + timedelta(minutes=2)) is False
