"""Foundry tracing helper の安全性テスト。"""

from src.config import AppSettings
from src.foundry_tracing import (
    get_app_insights_association_status,
    hash_identifier,
    is_foundry_tracing_enabled,
    redact_span_attribute_value,
    resolve_model_deployment,
    sanitize_span_attributes,
)


def _settings(**overrides: str) -> AppSettings:
    """必要な AppSettings をテスト用に組み立てる。"""
    values = {key: "" for key in AppSettings.__annotations__}
    values.update(
        {
            "model_name": "gpt-5-4-mini",
            "work_iq_timeout_seconds": "120",
            "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
            "environment": "development",
            "allowed_origins": "http://localhost:5173",
            "enable_foundry_tracing": "false",
        }
    )
    values.update(overrides)
    return AppSettings(**values)  # type: ignore[typeddict-item]


def test_foundry_tracing_is_gated_by_flag_project_and_app_insights() -> None:
    """flag / Project endpoint / App Insights 関連付けが揃った場合だけ有効化する。"""
    base = {
        "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
        "applicationinsights_connection_string": "InstrumentationKey=00000000-0000-0000-0000-000000000000",
    }

    assert is_foundry_tracing_enabled(_settings(**base)) is False
    assert is_foundry_tracing_enabled(_settings(**base, enable_foundry_tracing="true")) is True
    assert (
        is_foundry_tracing_enabled(
            _settings(enable_foundry_tracing="true", applicationinsights_connection_string=base["applicationinsights_connection_string"])
        )
        is False
    )
    assert (
        is_foundry_tracing_enabled(
            _settings(
                enable_foundry_tracing="true",
                project_endpoint=base["project_endpoint"],
                applicationinsights_connection_string="IngestionEndpoint=https://example.monitor.azure.com/",
            )
        )
        is False
    )


def test_app_insights_association_status_never_exposes_connection_string() -> None:
    """App Insights 判定結果は安全な reason のみを返す。"""
    status = get_app_insights_association_status(
        _settings(applicationinsights_connection_string="IngestionEndpoint=https://example.monitor.azure.com/")
    )

    assert status == {
        "configured": True,
        "associated": False,
        "reason": "missing_app_insights_identifier",
    }


def test_span_attribute_redaction_blocks_prompts_tokens_html_and_transcripts() -> None:
    """span 属性に本文・token・HTML・transcript を残さない。"""
    conversation_hash = hash_identifier("conversation-secret-id")
    attributes = sanitize_span_attributes(
        {
            "app.conversation.hash": conversation_hash,
            "gen_ai.request.model": "gpt-5-4-mini",
            "app.tool.names": ["web_search", "workiq_foundry_tool"],
            "app.work_iq.status": "completed",
            "llm.prompt": "夏の北海道旅行についての詳細な依頼本文",
            "mcp.auth.headers": "Authorization: Bearer secret-token",
            "brochure.html": "<html><body>secret brochure</body></html>",
            "voice.raw_transcript": "raw user transcript",
        }
    )

    assert attributes["app.conversation.hash"] == conversation_hash
    assert "conversation-secret-id" not in attributes["app.conversation.hash"]
    assert attributes["gen_ai.request.model"] == "gpt-5-4-mini"
    assert attributes["app.tool.names"] == ["web_search", "workiq_foundry_tool"]
    assert attributes["app.work_iq.status"] == "completed"
    assert str(attributes["llm.prompt"]).startswith("[redacted:")
    assert str(attributes["mcp.auth.headers"]).startswith("[redacted:")
    assert str(attributes["brochure.html"]).startswith("[redacted:")
    assert str(attributes["voice.raw_transcript"]).startswith("[redacted:")


def test_redaction_hashes_urls_and_long_values() -> None:
    """endpoint URL や長文値は hash 化する。"""
    assert str(redact_span_attribute_value("project.endpoint", "https://example.services.ai.azure.com/")).startswith(
        "[redacted:"
    )
    assert str(redact_span_attribute_value("safe.long", "x" * 200)).startswith("[redacted:")


def test_resolve_model_deployment_prefers_request_override() -> None:
    """モデル deployment は request override を優先し、本文設定は参照しない。"""
    settings = _settings(model_name="gpt-5-4-mini")

    assert resolve_model_deployment({"model": "gpt-5.5"}, settings) == "gpt-5.5"
    assert resolve_model_deployment({"temperature": 0.2}, settings) == "gpt-5-4-mini"
