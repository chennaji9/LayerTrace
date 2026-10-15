"""US5 契约测试：incidents 列表、acknowledge、silences 与降噪计数。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

pytestmark = [pytest.mark.contract]


def test_incident_page_counts() -> None:
    from packages.contracts.schemas import IncidentPage

    fields = set(IncidentPage.model_fields)
    assert {"items", "raw_alert_count", "incident_count", "notification_count"} <= fields


def test_silence_out_contract() -> None:
    from packages.contracts.schemas import SilenceOut

    fields = set(SilenceOut.model_fields)
    assert {"id", "created_at", "matcher", "reason", "starts_at", "ends_at"} <= fields


def test_acknowledge_body_contract() -> None:
    from apps.control_plane.api.incidents import AcknowledgeRequest

    req = AcknowledgeRequest(actor="ops")
    assert req.note is None
    with pytest.raises(ValidationError):
        AcknowledgeRequest(actor="")
