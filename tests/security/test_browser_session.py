"""Session integrity, expiration and CSRF binding do not expose the API token."""

import base64
import json

import pytest

from apps.control_plane.auth import (
    create_session_cookie,
    verify_session_cookie,
    verify_session_csrf,
)
from apps.control_plane.config import get_settings

pytestmark = [pytest.mark.security]


def test_session_cookie_signature_and_csrf(monkeypatch):
    monkeypatch.setenv("API_TOKENS", "only-secret-test-api-token")
    get_settings.cache_clear()
    try:
        value = create_session_cookie("only-secret-test-api-token", csrf_token="csrf-one")
        payload, signature = value.split(".")
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        assert "only-secret-test-api-token" not in str(data)
        assert verify_session_cookie(value).kind == "api"
        assert verify_session_csrf(value, "csrf-one")
        assert not verify_session_csrf(value, "csrf-other")
        assert verify_session_cookie(payload + "." + "0" * len(signature)) is None
        with pytest.raises(ValueError):
            create_session_cookie("wrong", csrf_token="csrf-one")
        monkeypatch.setattr("apps.control_plane.auth.time.time", lambda: data["expires"] + 1)
        assert verify_session_cookie(value) is None
    finally:
        get_settings.cache_clear()
