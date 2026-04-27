"""tool_telemetry の機密情報 redaction テスト。"""

from src.tool_telemetry import build_tool_event_data, redact_sensitive_mapping, redact_sensitive_text


def test_redact_sensitive_text_masks_tokens_headers_and_query_values() -> None:
    """Bearer token・API key・署名 query をマスクする。"""
    text = (
        "Authorization: Bearer abc.def.ghi api_key=plain-secret "
        "url=https://example.test/mcp?sig=url-secret&safe=value"
    )

    redacted = redact_sensitive_text(text)

    assert "abc.def.ghi" not in redacted
    assert "plain-secret" not in redacted
    assert "url-secret" not in redacted
    assert "Bearer [REDACTED]" in redacted
    assert "safe=value" in redacted


def test_redact_sensitive_mapping_masks_sensitive_keys_recursively() -> None:
    """mapping 内の機密キーとネストした文字列値をマスクする。"""
    payload = {
        "headers": {"Authorization": "Bearer token-value", "x-safe": "ok"},
        "message": "x-functions-key=secret-key",
        "items": [{"token": "nested-token"}, "password=pw-value"],
    }

    redacted = redact_sensitive_mapping(payload)

    assert redacted["headers"] == {"Authorization": "[REDACTED]", "x-safe": "ok"}
    assert redacted["message"] == "x-functions-key=[REDACTED]"
    assert redacted["items"] == [{"token": "[REDACTED]"}, "password=[REDACTED]"]


def test_build_tool_event_data_redacts_error_message() -> None:
    """tool_event の error_message は送信前に redaction される。"""
    payload = build_tool_event_data(
        "generate_improvement_brief",
        "failed",
        agent_name="improvement-mcp",
        error_code="HTTPError",
        error_message="request failed with Authorization: Bearer secret-token",
    )

    assert payload["error_message"] == "request failed with Authorization: Bearer [REDACTED]"
    assert "secret-token" not in payload["error_message"]
