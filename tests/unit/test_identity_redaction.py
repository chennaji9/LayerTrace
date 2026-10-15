"""Validated opaque Docker identities must survive incidental PII-like digit runs."""

from apps.control_plane.ingestion.redaction import redact_attributes


def test_full_docker_generation_identity_preserves_incidental_phone_digits():
    container = "a" * 20 + "13812345678" + "b" * 33
    instance = container + "@2026-10-14T16:08:20.430786695Z"
    values = {
        "container_id": container,
        "instance_id": instance,
        "model_instance_id": instance,
        "tool_instance_id": instance,
    }
    result = redact_attributes(values)
    assert result.attributes == values
    assert result.redacted_value_keys == []


def test_identity_keys_do_not_bypass_pii_or_credential_text_redaction():
    result = redact_attributes(
        {
            "instance_id": "call 13812345678",
            "container_id": "sk-" + "x" * 20,
            "message": "call 13812345678",
        }
    )
    assert result.attributes["instance_id"] == "call [REDACTED:phone]"
    assert result.attributes["container_id"] == "[REDACTED:credential]"
    assert result.attributes["message"] == "call [REDACTED:phone]"
