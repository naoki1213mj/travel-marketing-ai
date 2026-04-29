"""チャット逐次オーケストレーションのテスト"""

import asyncio
import json
import urllib.parse
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.requests import Request

from src import config as config_module
from src.api import chat as chat_module


def _parse_sse(event: str) -> tuple[str, dict]:
    lines = event.strip().split("\n")
    event_name = lines[0].replace("event: ", "")
    payload = json.loads(lines[1].replace("data: ", "", 1)) if len(lines) > 1 else {}
    return event_name, payload


class TestExtractOauthConsentLink:
    """_extract_oauth_consent_link のテスト"""

    def test_prefers_camel_case_attribute(self):
        consent_request = MagicMock()
        consent_request.consentLink = " https://example.com/consent "
        consent_request.consent_link = ""

        assert chat_module._extract_oauth_consent_link(consent_request) == "https://example.com/consent"

    def test_falls_back_to_as_dict_payload(self):
        consent_request = MagicMock()
        consent_request.consentLink = ""
        consent_request.consent_link = ""
        consent_request.as_dict.return_value = {"auth_uri": "https://example.com/auth"}

        assert chat_module._extract_oauth_consent_link(consent_request) == "https://example.com/auth"


class TestExtractTerminalToolEvents:
    """_extract_terminal_tool_events のテスト"""

    def test_keeps_error_message_containing_data_prefix(self) -> None:
        event = chat_module.format_sse(
            chat_module.SSEEventType.TOOL_EVENT,
            {
                "tool": "foundry_prompt_agent",
                "status": "failed",
                "error_message": "OpenAI rejected request: Invalid data: field is required",
            },
        )

        matched = chat_module._extract_terminal_tool_events(
            [event],
            tool_names={"foundry_prompt_agent"},
            statuses={"failed"},
        )

        assert matched == [event]
        _, payload = _parse_sse(matched[0])
        assert payload["error_message"] == "OpenAI rejected request: Invalid data: field is required"


class TestSSEEventPersistenceParsing:
    """SSE イベント保存用 parser の後方互換テスト"""

    def test_record_sse_event_preserves_data_prefix_inside_json_strings(self) -> None:
        collected: list[dict] = []
        event = chat_module.format_sse(
            chat_module.SSEEventType.TOOL_EVENT,
            {
                "tool": "foundry_prompt_agent",
                "status": "failed",
                "error_message": "OpenAI rejected request: Invalid data: field is required",
            },
        )

        chat_module._record_sse_event(collected, event, 0.0)

        assert collected[0]["data"]["error_message"] == "OpenAI rejected request: Invalid data: field is required"

    def test_sse_to_event_dict_preserves_data_prefix_inside_json_strings(self) -> None:
        event = chat_module.format_sse(
            chat_module.SSEEventType.ERROR,
            {"message": "Invalid data: field is required", "code": "BAD_PAYLOAD"},
        )

        converted = chat_module._sse_to_event_dict(event)

        assert converted is not None
        assert converted["data"]["message"] == "Invalid data: field is required"


class TestNormalizeModelSettings:
    """_normalize_model_settings のテスト"""

    def test_resolves_configured_gpt55_deployment_and_supported_image_settings(self, monkeypatch) -> None:
        """GPT-5.5 は設定済み deployment 名へ解決して残す。"""
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
        monkeypatch.setenv("ENABLE_GPT_55", "true")
        monkeypatch.setenv("GPT_55_DEPLOYMENT_NAME", "gpt-5-5-prod")

        normalized = chat_module._normalize_model_settings(
            {
                "model": "gpt-5.5",
                "temperature": 0.4,
                "max_tokens": 4096,
                "top_p": 0.9,
                "iq_search_results": 8,
                "iq_score_threshold": 0.25,
                "image_settings": {
                    "image_model": "gpt-image-2",
                    "image_quality": "high",
                    "image_width": 1024,
                    "image_height": 1024,
                    "unexpected": "ignored",
                },
                "unexpected": "ignored",
            }
        )

        assert normalized == {
            "model": "gpt-5-5-prod",
            "temperature": 0.4,
            "max_tokens": 4096,
            "top_p": 0.9,
            "iq_search_results": 8,
            "iq_score_threshold": 0.25,
            "image_settings": {
                "image_model": "gpt-image-2",
                "image_quality": "high",
                "image_width": 1024,
                "image_height": 1024,
            },
        }

    def test_resolves_configured_model_router_deployment(self, monkeypatch) -> None:
        """Model Router は明示有効化時だけ deployment として受け付ける。"""
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
        monkeypatch.setenv("ENABLE_MODEL_ROUTER", "true")
        monkeypatch.setenv("MODEL_ROUTER_DEPLOYMENT_NAME", "router-prod")

        normalized = chat_module._normalize_model_settings({"model": "model-router"})

        assert normalized == {"model": "router-prod"}

    def test_rejects_unavailable_optional_model(self, monkeypatch) -> None:
        """未設定の optional model は明示的な MODEL_DEPLOYMENT_UNAVAILABLE にする。"""
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
        monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
        monkeypatch.delenv("ENABLE_GPT_55", raising=False)
        monkeypatch.delenv("GPT_55_AVAILABLE", raising=False)
        monkeypatch.delenv("GPT_55_DEPLOYMENT_NAME", raising=False)
        monkeypatch.delenv("GPT_5_5_DEPLOYMENT_NAME", raising=False)

        with pytest.raises(chat_module.ModelDeploymentUnavailableError):
            chat_module._normalize_model_settings({"model": "gpt-5.5"})


# --- _extract_result_text テスト ---


class TestExtractResultText:
    """_extract_result_text のテスト"""

    def test_none_returns_empty(self):
        assert chat_module._extract_result_text(None) == ""

    def test_direct_message_text(self):
        """message.contents[].text からテキストを取得できること"""
        content = MagicMock()
        content.text = "直接テキスト"
        result = MagicMock()
        result.contents = [content]
        assert chat_module._extract_result_text(result) == "直接テキスト"

    def test_fallback_to_get_outputs(self):
        """contents がない場合 get_outputs() から取得"""
        inner_content = MagicMock()
        inner_content.text = "出力テキスト"
        inner_msg = MagicMock()
        inner_msg.contents = [inner_content]

        result = MagicMock()
        result.contents = None
        result.get_outputs.return_value = [[inner_msg]]
        assert chat_module._extract_result_text(result) == "出力テキスト"

    def test_fallback_to_str(self):
        """get_outputs も空の場合 str(result) にフォールバック"""
        result = MagicMock()
        result.contents = None
        result.get_outputs.return_value = []
        result.__str__ = lambda self: "fallback string"
        assert chat_module._extract_result_text(result) == "fallback string"

    def test_get_outputs_raises_exception(self):
        """get_outputs() が例外を投げた場合"""
        result = MagicMock()
        result.contents = None
        result.get_outputs.side_effect = RuntimeError("broken")
        result.__str__ = lambda self: "error fallback"
        assert chat_module._extract_result_text(result) == "error fallback"

    def test_multiple_messages_returns_last(self):
        """複数 message の場合、最後の非空テキストを返す"""
        c1 = MagicMock()
        c1.text = "第1メッセージ"
        msg1 = MagicMock()
        msg1.contents = [c1]

        c2 = MagicMock()
        c2.text = "第2メッセージ"
        msg2 = MagicMock()
        msg2.contents = [c2]

        result = MagicMock()
        result.contents = None
        result.get_outputs.return_value = [msg1, msg2]
        assert chat_module._extract_result_text(result) == "第2メッセージ"

    def test_output_text_strips_foundry_citation_markers(self):
        """Foundry/Web Search citation marker はユーザー表示前に除去する"""
        result = MagicMock()
        result.output_text = "需要が高い。 \ue200cite\ue202turn0search0\ue201"

        assert chat_module._extract_result_text(result) == "需要が高い。"

    def test_message_text_strips_multiple_foundry_citation_markers(self):
        """複数 citation marker もまとめて除去する"""
        content = MagicMock()
        content.text = "市場は拡大中。 \ue200cite\ue202turn0search0, turn0search1\ue201"
        result = MagicMock()
        result.contents = [content]

        assert chat_module._extract_result_text(result) == "市場は拡大中。"


class TestWebSearchEvidence:
    """Web Search citation の evidence 変換テスト"""

    def test_extract_web_search_evidence_from_url_annotations(self):
        annotation = SimpleNamespace(type="url_citation", url="https://example.com/safety", title="Safety report")
        content = SimpleNamespace(annotations=[annotation])
        result = SimpleNamespace(output=[SimpleNamespace(content=[content])])

        evidence = chat_module._extract_web_search_evidence(result, "安全情報を確認しました。")

        assert evidence == [
            {
                "id": "web-search-1",
                "title": "Safety report",
                "source": "web",
                "url": "https://example.com/safety",
                "relevance": 0.75,
                "metadata": {"provider": "foundry_web_search"},
            }
        ]

    def test_extract_web_search_evidence_falls_back_to_summary(self):
        evidence = chat_module._extract_web_search_evidence(SimpleNamespace(output=[]), "市場トレンドを確認しました。")

        assert evidence[0]["source"] == "web"
        assert evidence[0]["quote"] == "市場トレンドを確認しました。"


class TestTokenUsageMetrics:
    """token usage と概算コストの抽出テスト"""

    def test_extracts_responses_api_usage_aliases(self) -> None:
        result = SimpleNamespace(usage={"input_tokens": "120", "output_tokens": 30})

        usage = chat_module._extract_token_usage(result)

        assert usage == {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        assert chat_module._extract_total_tokens(result) == 150

    def test_extracts_nested_output_usage_without_text_content(self) -> None:
        output = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        result = SimpleNamespace(get_outputs=lambda: [[output]])

        assert chat_module._extract_token_usage(result) == {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    def test_estimated_cost_requires_enable_cost_metrics(self, monkeypatch) -> None:
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")
        monkeypatch.delenv("ENABLE_COST_METRICS", raising=False)
        usage: chat_module.TokenUsage = {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}

        assert chat_module._estimate_cost_usd(usage) is None

        monkeypatch.setenv("ENABLE_COST_METRICS", "true")
        assert chat_module._estimate_cost_usd(usage) == 0.00125

    def test_build_done_metrics_adds_agent_metrics_additively(self, monkeypatch) -> None:
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.setenv("ENABLE_COST_METRICS", "true")
        metrics = chat_module._build_done_metrics(
            latency_seconds=2.5,
            tool_calls=3,
            total_tokens=30,
            prompt_tokens=10,
            completion_tokens=20,
            agent_metrics={
                "marketing-plan-agent": {
                    "latency_seconds": 1.2,
                    "total_tokens": 30,
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "estimated_cost_usd": 0.002,
                }
            },
        )

        assert metrics["latency_seconds"] == 2.5
        assert metrics["total_tokens"] == 30
        assert metrics["prompt_tokens"] == 10
        assert metrics["agent_latencies"] == {"marketing-plan-agent": 1.2}
        assert metrics["agent_tokens"] == {"marketing-plan-agent": 30}
        assert metrics["estimated_cost_usd"] == 0.002

    def test_build_done_metrics_omits_agent_metrics_when_flag_disabled(self, monkeypatch) -> None:
        monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
        monkeypatch.delenv("ENABLE_COST_METRICS", raising=False)

        metrics = chat_module._build_done_metrics(
            latency_seconds=2.5,
            tool_calls=3,
            total_tokens=30,
            agent_metrics={
                "marketing-plan-agent": {
                    "latency_seconds": 1.2,
                    "total_tokens": 30,
                    "estimated_cost_usd": 0.002,
                }
            },
        )

        assert "agent_latencies" not in metrics
        assert "estimated_cost_usd" not in metrics


class TestExtractLatestEvaluationResult:
    """_extract_latest_evaluation_result のテスト"""

    def test_ignores_malformed_version_during_evaluation_rerun(self) -> None:
        conversation = {
            "messages": [
                {
                    "event": "evaluation_result",
                    "data": {"version": "draft", "result": {"builtin": {"relevance": {"score": 1}}}},
                },
                {
                    "event": "evaluation_result",
                    "data": {"version": 2, "result": {"builtin": {"relevance": {"score": 4}}}},
                },
            ]
        }

        result = chat_module._extract_latest_evaluation_result(conversation, artifact_version=2)

        assert result == {"builtin": {"relevance": {"score": 4}}}


# --- _extract_brochure_html テスト ---


class TestExtractBrochureHtml:
    """_extract_brochure_html のテスト"""

    def test_code_block_html(self):
        text = "```html\n<html><body>Hello</body></html>\n```"
        result = chat_module._extract_brochure_html(text)
        assert result is not None
        assert result.startswith("<html>")

    def test_code_block_html_case_insensitive(self):
        text = "```HTML\n<div>content</div>\n```"
        result = chat_module._extract_brochure_html(text)
        assert result is not None

    def test_doctype_html_fallback(self):
        text = "Some text before\n<!DOCTYPE html>\n<html><body>Content</body></html>"
        result = chat_module._extract_brochure_html(text)
        assert result is not None
        assert result.startswith("<!DOCTYPE html>")

    def test_html_tag_fallback(self):
        text = 'Prefix text\n<html lang="ja"><body>コンテンツ</body></html>'
        result = chat_module._extract_brochure_html(text)
        assert result is not None
        assert '<html lang="ja">' in result

    def test_no_html_returns_none(self):
        text = "This is just plain text without any HTML"
        result = chat_module._extract_brochure_html(text)
        assert result is None

    def test_empty_string_returns_none(self):
        result = chat_module._extract_brochure_html("")
        assert result is None


class TestExtractPlanSummary:
    """_extract_plan_summary のテスト"""

    def test_skips_reference_lines_and_headings(self):
        markdown = """[参考パンフレット: C:/tmp/ref.pdf]
## タイトル
春の北海道絶景ツアー
## キャッチコピー
雪景色と温泉を満喫
## プラン概要
美食と雪遊びを楽しむ旅
"""

        result = chat_module._extract_plan_summary(markdown)

        assert "参考パンフレット" not in result
        assert "タイトル" not in result
        assert result.startswith("春の北海道絶景ツアー。雪景色と温泉を満喫")


class TestMarketingPlanRuntimeSettings:
    """marketing-plan-agent runtime selector のテスト"""

    def test_resolve_runtime_defaults_to_foundry_preprovisioned(self, monkeypatch) -> None:
        monkeypatch.setattr(chat_module, "get_settings", lambda: {"marketing_plan_runtime": "foundry_preprovisioned"})
        assert chat_module._resolve_marketing_plan_runtime(None) == "foundry_preprovisioned"

    def test_resolve_runtime_request_override_wins(self, monkeypatch) -> None:
        monkeypatch.setattr(chat_module, "get_settings", lambda: {"marketing_plan_runtime": "foundry_preprovisioned"})
        assert (
            chat_module._resolve_marketing_plan_runtime({"marketing_plan_runtime": "legacy"})
            == "legacy"
        )

    def test_resolve_runtime_accepts_foundry_prompt_alias(self, monkeypatch) -> None:
        monkeypatch.setattr(chat_module, "get_settings", lambda: {"marketing_plan_runtime": "foundry_prompt"})
        assert chat_module._resolve_marketing_plan_runtime(None) == "foundry_preprovisioned"

    def test_build_effective_workflow_settings_includes_runtime(self, monkeypatch) -> None:
        monkeypatch.setattr(
            chat_module,
            "get_settings",
            lambda: {"marketing_plan_runtime": "foundry_preprovisioned", "work_iq_runtime": "foundry_tool"},
        )
        assert chat_module._build_effective_workflow_settings(
            {
                "manager_approval_enabled": True,
                "manager_email": "manager@example.com",
            }
        ) == {
            "manager_approval_enabled": True,
            "manager_email": "manager@example.com",
            "marketing_plan_runtime": "foundry_preprovisioned",
            "work_iq_runtime": "foundry_tool",
        }

    def test_parse_saved_workflow_settings_keeps_backward_compatibility(self) -> None:
        assert chat_module._parse_saved_workflow_settings(
            {
                "manager_approval_enabled": True,
                "manager_email": "manager@example.com",
            }
        ) == {
            "manager_approval_enabled": True,
            "manager_email": "manager@example.com",
        }

    def test_resolve_work_iq_runtime_defaults_to_foundry_tool(self, monkeypatch) -> None:
        monkeypatch.setattr(chat_module, "get_settings", lambda: {"work_iq_runtime": "foundry_tool"})
        assert chat_module._resolve_work_iq_runtime(None) == "foundry_tool"

    def test_resolve_work_iq_timeout_seconds_caps_foundry_timeout(self, monkeypatch) -> None:
        monkeypatch.setattr(chat_module, "get_settings", lambda: {"work_iq_timeout_seconds": "120"})
        assert chat_module._resolve_work_iq_timeout_seconds() == 95.0

    def test_foundry_work_iq_no_longer_auto_falls_back(self) -> None:
        event = chat_module.format_sse(
            chat_module.SSEEventType.TOOL_EVENT,
            {
                "tool": "workiq_foundry_tool",
                "status": "failed",
                "error_code": "WORKIQ_NOT_USED",
            },
        )
        assert chat_module._should_retry_marketing_plan_with_graph_prefetch(
            {"success": False, "events": [event]}
        ) is False

    def test_foundry_work_iq_obo_auth_failure_can_fall_back_to_graph_prefetch(self) -> None:
        event = chat_module.format_sse(
            chat_module.SSEEventType.TOOL_EVENT,
            {
                "tool": "workiq_foundry_tool",
                "status": "auth_required",
                "error_code": "WORKIQ_OBO_TOKEN_FAILED",
            },
        )
        assert chat_module._should_retry_marketing_plan_with_graph_prefetch(
            {"success": False, "events": [event]}
        ) is True

    def test_build_effective_workflow_settings_rejects_legacy_foundry_tool_combo(self, monkeypatch) -> None:
        monkeypatch.setattr(
            chat_module,
            "get_settings",
            lambda: {"marketing_plan_runtime": "legacy", "work_iq_runtime": "foundry_tool"},
        )
        with pytest.raises(ValueError, match="work_iq_runtime=foundry_tool"):
            chat_module._build_effective_workflow_settings(
                {
                    "manager_approval_enabled": False,
                    "manager_email": "",
                }
            )

    def test_build_effective_workflow_settings_ignores_foundry_tool_when_work_iq_off(self, monkeypatch) -> None:
        """Work IQ OFF では foundry_tool 既定値を legacy runtime の妨げにしない。"""
        monkeypatch.setattr(
            chat_module,
            "get_settings",
            lambda: {"marketing_plan_runtime": "legacy", "work_iq_runtime": "foundry_tool"},
        )

        assert chat_module._build_effective_workflow_settings(
            {
                "manager_approval_enabled": False,
                "manager_email": "",
            },
            work_iq_enabled=False,
        ) == {
            "manager_approval_enabled": False,
            "manager_email": "",
            "marketing_plan_runtime": "legacy",
            "work_iq_runtime": "graph_prefetch",
        }


@pytest.mark.asyncio
async def test_execute_agent_uses_legacy_marketing_agent_when_work_iq_is_off(monkeypatch) -> None:
    """Work IQ OFF では Work IQ 付き Prompt Agent を避けて legacy 経路を使う。"""

    captured: dict[str, object] = {}

    class _FakeLegacyAgent:
        async def run(self, user_input: str):
            captured["legacy_user_input"] = user_input
            return SimpleNamespace(output_text="legacy output")

    def fake_create_marketing_plan_agent(model_settings: dict | None = None):
        captured["legacy_model_settings"] = model_settings
        return _FakeLegacyAgent()

    def fake_run_marketing_plan_prompt_agent(*args, **kwargs):
        del args, kwargs
        raise AssertionError("Foundry prompt agent should not run when Work IQ is off")

    monkeypatch.setattr("src.agents.create_marketing_plan_agent", fake_create_marketing_plan_agent)
    monkeypatch.setattr("src.foundry_prompt_agents.run_marketing_plan_prompt_agent", fake_run_marketing_plan_prompt_agent)

    outcome = await chat_module._execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input="沖縄プラン",
        conversation_id="conv-foundry-no-workiq",
        model_settings={"model": "gpt-5-4-mini"},
        workflow_settings={"marketing_plan_runtime": "foundry_preprovisioned"},
        work_iq_access_token="",
    )

    assert outcome["success"] is True
    assert outcome["text"] == "legacy output"
    assert captured == {
        "legacy_user_input": "沖縄プラン",
        "legacy_model_settings": {"model": "gpt-5-4-mini"},
    }
    parsed = [_parse_sse(event) for event in outcome["events"]]
    assert any(
        event_name == chat_module.SSEEventType.AGENT_PROGRESS
        and payload.get("agent") == "marketing-plan-agent"
        and payload.get("status") == "completed"
        for event_name, payload in parsed
    )
    assert not any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") in {"foundry_prompt_agent", "workiq_foundry_tool"}
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_execute_agent_brochure_timeout_returns_fallback(monkeypatch) -> None:
    """ブローシャ生成が長時間化しても bounded fallback で SSE を完了する。"""

    class _SlowBrochureAgent:
        async def run(self, user_input: str):
            del user_input
            await asyncio.sleep(1)
            return SimpleNamespace(output_text="<html><body>too late</body></html>")

    def fake_create_brochure_agent(model_settings: dict | None = None):
        del model_settings
        return _SlowBrochureAgent()

    monkeypatch.setattr(chat_module, "_BROCHURE_AGENT_MAX_WAIT_SECONDS", 0.01)
    monkeypatch.setattr("src.agents.create_brochure_gen_agent", fake_create_brochure_agent)

    outcome = await chat_module._execute_agent(
        agent_name="brochure-gen-agent",
        agent_step=5,
        user_input="# テスト旅行プラン\n規制チェック済みの企画書本文",
        conversation_id="conv-brochure-timeout",
        model_settings={"model": "gpt-5-4-mini"},
        include_done=True,
    )

    parsed = [_parse_sse(event) for event in outcome["events"]]

    assert outcome["success"] is True
    assert "フォールバック生成" in outcome["text"]
    assert any(
        event_name == chat_module.SSEEventType.AGENT_PROGRESS
        and payload.get("agent") == "brochure-gen-agent"
        and payload.get("status") == "completed"
        for event_name, payload in parsed
    )
    assert parsed[-1][0] == chat_module.SSEEventType.DONE


@pytest.mark.asyncio
async def test_execute_agent_does_not_fallback_when_work_iq_token_is_missing(monkeypatch) -> None:
    """Work IQ ON の delegated token 欠落は legacy fallback で隠さない。"""

    def fake_create_marketing_plan_agent(model_settings: dict | None = None):
        del model_settings
        raise AssertionError("Legacy marketing agent should not run when Work IQ auth is missing")

    def fake_run_marketing_plan_prompt_agent(*args, **kwargs):
        del args, kwargs
        raise ValueError("Work IQ is enabled for the Foundry marketing-plan path, but no delegated access token was supplied.")

    monkeypatch.setattr("src.agents.create_marketing_plan_agent", fake_create_marketing_plan_agent)
    monkeypatch.setattr("src.foundry_prompt_agents.run_marketing_plan_prompt_agent", fake_run_marketing_plan_prompt_agent)

    outcome = await chat_module._execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input="沖縄プラン",
        conversation_id="conv-workiq-missing-token",
        model_settings={"model": "gpt-5-4-mini"},
        workflow_settings={
            "marketing_plan_runtime": "foundry_preprovisioned",
            "work_iq_runtime": "foundry_tool",
        },
        work_iq_session={"enabled": True, "source_scope": ["emails"]},
        work_iq_access_token="",
    )

    assert outcome["success"] is False
    parsed = [_parse_sse(event) for event in outcome["events"]]
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and "no delegated access token" in str(payload.get("error_message", ""))
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_execute_agent_maps_foundry_work_iq_obo_failure_to_auth_required(monkeypatch) -> None:
    """Foundry Work IQ OBO 失敗は汎用 Agent エラーではなく再サインイン要求にする。"""

    def fake_create_marketing_plan_agent(model_settings: dict | None = None):
        del model_settings
        raise AssertionError("Legacy marketing agent should not run for Work IQ OBO failures")

    def fake_run_marketing_plan_prompt_agent(*args, **kwargs):
        del args, kwargs
        raise RuntimeError(
            "Error code: 400 - {'error': {'message': 'Failed to fetch access token. "
            'Status: BadRequest. Details: "ARA OBO token request failed with status BadRequest", '
            "'code': 'tool_user_error'}}"
        )

    monkeypatch.setattr("src.agents.create_marketing_plan_agent", fake_create_marketing_plan_agent)
    monkeypatch.setattr("src.foundry_prompt_agents.run_marketing_plan_prompt_agent", fake_run_marketing_plan_prompt_agent)

    outcome = await chat_module._execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input="沖縄プラン",
        conversation_id="conv-workiq-obo-failure",
        model_settings={"model": "gpt-5-4-mini"},
        workflow_settings={
            "marketing_plan_runtime": "foundry_preprovisioned",
            "work_iq_runtime": "foundry_tool",
        },
        work_iq_session={"enabled": True, "source_scope": ["emails"]},
        work_iq_access_token="delegated-token",
    )

    assert outcome["success"] is False
    parsed = [_parse_sse(event) for event in outcome["events"]]
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") == "workiq_foundry_tool"
        and payload.get("status") == "auth_required"
        and payload.get("error_code") == "WORKIQ_OBO_TOKEN_FAILED"
        for event_name, payload in parsed
    )
    assert any(
        event_name == chat_module.SSEEventType.ERROR and payload.get("code") == "WORKIQ_AUTH_REQUIRED"
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_execute_agent_maps_foundry_work_iq_timeout_to_unavailable(monkeypatch) -> None:
    """Foundry Work IQ timeout は汎用 Agent エラーではなく Work IQ timeout にする。"""

    def fake_create_marketing_plan_agent(model_settings: dict | None = None):
        del model_settings
        raise AssertionError("Legacy marketing agent should not run for Work IQ timeout failures")

    def fake_run_marketing_plan_prompt_agent(*args, **kwargs):
        del args, kwargs
        raise TimeoutError("Foundry Work IQ connector timed out after 95s")

    monkeypatch.setattr("src.agents.create_marketing_plan_agent", fake_create_marketing_plan_agent)
    monkeypatch.setattr("src.foundry_prompt_agents.run_marketing_plan_prompt_agent", fake_run_marketing_plan_prompt_agent)

    outcome = await chat_module._execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input="沖縄プラン",
        conversation_id="conv-workiq-timeout",
        model_settings={"model": "gpt-5-4-mini"},
        workflow_settings={
            "marketing_plan_runtime": "foundry_preprovisioned",
            "work_iq_runtime": "foundry_tool",
        },
        work_iq_session={"enabled": True, "source_scope": ["emails"]},
        work_iq_access_token="delegated-token",
    )

    assert outcome["success"] is False
    parsed = [_parse_sse(event) for event in outcome["events"]]
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") == "workiq_foundry_tool"
        and payload.get("status") == "timeout"
        and payload.get("error_code") == "WORKIQ_TIMEOUT"
        for event_name, payload in parsed
    )
    assert any(
        event_name == chat_module.SSEEventType.ERROR and payload.get("code") == "WORKIQ_UNAVAILABLE"
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_execute_agent_falls_back_to_legacy_when_foundry_prompt_agent_is_unavailable(monkeypatch) -> None:
    """Foundry Agent 未作成時は Agent Framework 経路へ 1 回だけフォールバックする。"""

    captured: dict[str, object] = {"foundry_calls": 0, "legacy_calls": 0}

    class _FakeLegacyAgent:
        async def run(self, user_input: str):
            captured["legacy_calls"] += 1
            captured["legacy_user_input"] = user_input
            return SimpleNamespace(output_text="legacy fallback output")

    def fake_create_marketing_plan_agent(model_settings: dict | None = None):
        captured["legacy_model_settings"] = model_settings
        return _FakeLegacyAgent()

    def fake_run_marketing_plan_prompt_agent(*args, **kwargs):
        del args, kwargs
        captured["foundry_calls"] += 1
        raise ValueError("marketing-plan Foundry Agent が未作成です")

    monkeypatch.setattr("src.agents.create_marketing_plan_agent", fake_create_marketing_plan_agent)
    monkeypatch.setattr("src.foundry_prompt_agents.run_marketing_plan_prompt_agent", fake_run_marketing_plan_prompt_agent)

    outcome = await chat_module._execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input="北海道プラン",
        conversation_id="conv-foundry-fallback",
        model_settings={"model": "gpt-5-4-mini"},
        workflow_settings={"marketing_plan_runtime": "foundry_preprovisioned"},
        work_iq_session={"enabled": True, "source_scope": ["emails"]},
        work_iq_access_token="delegated-token",
    )

    assert outcome["success"] is True
    assert outcome["text"] == "legacy fallback output"
    assert captured == {
        "foundry_calls": 1,
        "legacy_calls": 1,
        "legacy_user_input": "北海道プラン",
        "legacy_model_settings": {"model": "gpt-5-4-mini"},
    }


# --- _extract_inline_images テスト ---


class TestExtractInlineImages:
    """_extract_inline_images のテスト"""

    def test_data_uri_images(self):
        html = '<html><body><img src="data:image/png;base64,abc123" alt="Test" /></body></html>'
        images = chat_module._extract_inline_images(html)
        assert len(images) == 1
        assert images[0]["url"] == "data:image/png;base64,abc123"
        assert images[0]["alt"] == "Test"

    def test_no_images(self):
        html = "<html><body><p>No images</p></body></html>"
        images = chat_module._extract_inline_images(html)
        assert images == []

    def test_non_data_uri_images_ignored(self):
        html = '<img src="https://example.com/image.png" alt="External" />'
        images = chat_module._extract_inline_images(html)
        assert images == []

    def test_multiple_images(self):
        html = (
            '<img src="data:image/png;base64,aaa" alt="First" /><img src="data:image/jpeg;base64,bbb" alt="Second" />'
        )
        images = chat_module._extract_inline_images(html)
        assert len(images) == 2

    def test_missing_alt_uses_default(self):
        html = '<img src="data:image/png;base64,abc" />'
        images = chat_module._extract_inline_images(html)
        assert images[0]["alt"] == "Generated image"


# --- _extract_code_interpreter_images テスト ---


class TestExtractCodeInterpreterImages:
    """_extract_code_interpreter_images のテスト"""

    def test_no_get_outputs(self):
        """get_outputs が無い場合は空リスト"""
        result = MagicMock(spec=[])
        images = chat_module._extract_code_interpreter_images(result)
        assert images == []

    def test_get_outputs_raises_exception(self):
        result = MagicMock()
        result.get_outputs.side_effect = RuntimeError("error")
        images = chat_module._extract_code_interpreter_images(result)
        assert images == []

    def test_code_interpreter_with_base64_image(self):
        """base64 画像データを含む code_interpreter_call"""
        image_obj = MagicMock()
        image_obj.data = "base64data"
        image_obj.b64_json = ""
        image_obj.file_id = ""

        ci_out = MagicMock()
        ci_out.type = "image"
        ci_out.image = image_obj

        ci_result = MagicMock()
        ci_result.outputs = [ci_out]

        item = MagicMock()
        item.type = "code_interpreter_call"
        item.code_interpreter = ci_result

        result = MagicMock()
        result.get_outputs.return_value = [item]

        images = chat_module._extract_code_interpreter_images(result)
        assert len(images) == 1
        assert images[0]["url"].startswith("data:image/png;base64,")

    def test_non_code_interpreter_items_skipped(self):
        """code_interpreter_call 以外のアイテムは無視"""
        item = MagicMock()
        item.type = "text"

        result = MagicMock()
        result.get_outputs.return_value = [item]

        images = chat_module._extract_code_interpreter_images(result)
        assert images == []

    def test_nested_list_outputs(self):
        """get_outputs がリストのリストを返す場合"""
        item = MagicMock()
        item.type = "text"

        result = MagicMock()
        result.get_outputs.return_value = [[item]]

        images = chat_module._extract_code_interpreter_images(result)
        assert images == []


# --- _is_approval_response テスト ---


class TestIsApprovalResponse:
    """_is_approval_response のテスト"""

    @pytest.mark.parametrize(
        "text",
        [
            "承認",
            "了承",
            "進めて",
            "批准",
            "同意",
            "approve",
            "approved",
            "ok",
            "yes",
            "go",
            "  承認  ",
            "APPROVE",
            "OK",
        ],
    )
    def test_approval_keywords(self, text):
        assert chat_module._is_approval_response(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "キャッチコピーを修正して",
            "もっと詳しく",
            "却下",
            "no",
            "",
        ],
    )
    def test_non_approval_keywords(self, text):
        assert chat_module._is_approval_response(text) is False


# --- _build_marketing_plan_prompt テスト ---


class TestBuildMarketingPlanPrompt:
    """_build_marketing_plan_prompt のテスト"""

    def test_contains_user_input_and_analysis(self):
        result = chat_module._build_marketing_plan_prompt("沖縄プラン", "売上データ分析")
        assert "沖縄プラン" in result
        assert "売上データ分析" in result
        assert "ユーザー依頼" in result
        assert "Agent1 の分析結果" in result

    def test_includes_work_iq_brief_when_available(self):
        result = chat_module._build_marketing_plan_prompt(
            "沖縄プラン",
            "売上データ分析",
            {
                "enabled": True,
                "source_scope": ["emails"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
                "brief_summary": "メールで家族向け訴求を重視していました。",
                "brief_source_metadata": [{"source": "emails", "label": "メール", "count": 2}],
            },
        )
        assert "Work IQ の職場コンテキスト" in result
        assert "家族向け訴求" in result
        assert "メール: 2 件" in result

    def test_includes_work_iq_tool_guidance_for_foundry_tool(self):
        result = chat_module._build_marketing_plan_prompt(
            "沖縄プラン",
            "売上データ分析",
            {
                "enabled": True,
                "source_scope": ["emails", "teams_chats"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            "foundry_tool",
        )
        assert "Microsoft 365 参照ガイド" in result
        assert "メール" in result
        assert "Teams チャット" in result

    def test_rejects_insufficient_analysis(self):
        with pytest.raises(ValueError, match="Agent1 の分析結果が不足"):
            chat_module._build_marketing_plan_prompt("沖縄プラン", "分析")


# --- _build_revision_prompt テスト ---


class TestBuildRevisionPrompt:
    """_build_revision_prompt のテスト"""

    def test_contains_all_context(self):
        context = {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "企画書内容",
            "model_settings": None,
        }
        result = chat_module._build_revision_prompt(context, "キャッチコピーを変更")
        assert "沖縄プラン" in result
        assert "分析結果" in result
        assert "企画書内容" in result
        assert "キャッチコピーを変更" in result

    def test_includes_work_iq_brief_when_saved(self):
        context = {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "企画書内容",
            "model_settings": None,
            "work_iq_session": {
                "enabled": True,
                "source_scope": ["teams_chats"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
                "brief_summary": "Teams で沖縄より北海道案の反応が良かったです。",
            },
        }
        result = chat_module._build_revision_prompt(context, "キャッチコピーを変更")
        assert "Work IQ の職場コンテキスト" in result
        assert "北海道案の反応" in result


# --- _extract_plan_title テスト ---


class TestExtractPlanTitle:
    """_extract_plan_title のテスト"""

    def test_heading_extraction(self):
        md = "# 春の沖縄プラン\n\n## 概要\nsome content"
        assert chat_module._extract_plan_title(md) == "春の沖縄プラン"

    def test_no_heading_returns_default(self):
        md = "テキストのみ"
        assert chat_module._extract_plan_title(md) == "旅行マーケティング企画書"

    def test_h2_heading(self):
        md = "## サマープラン\ncontent"
        assert chat_module._extract_plan_title(md) == "サマープラン"


# --- _sanitize_text テスト ---


class TestSanitizeText:
    """_sanitize_text のテスト"""

    def test_strips_whitespace(self):
        assert chat_module._sanitize_text("  hello  ") == "hello"

    def test_removes_control_chars(self):
        assert chat_module._sanitize_text("hello\x00world") == "helloworld"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="空"):
            chat_module._sanitize_text("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="空"):
            chat_module._sanitize_text("   ")


# --- _build_content_events テスト ---


class TestBuildContentEvents:
    """_build_content_events のテスト"""

    def test_brochure_agent_with_html(self):
        text = "```html\n<html><body>Content</body></html>\n```"
        events = chat_module._build_content_events("brochure-gen-agent", text)
        assert len(events) >= 1
        _, payload = _parse_sse(events[0])
        assert payload["content_type"] == "html"

    def test_brochure_agent_with_html_and_images(self):
        text = '```html\n<html><body><img src="data:image/png;base64,abc" alt="hero" /></body></html>\n```'
        events = chat_module._build_content_events("brochure-gen-agent", text)
        assert len(events) == 2  # text + image
        _, img_payload = _parse_sse(events[1])
        assert img_payload["url"] == "data:image/png;base64,abc"

    def test_non_brochure_agent_text(self):
        events = chat_module._build_content_events("data-search-agent", "分析結果テキスト")
        assert len(events) == 1
        _, payload = _parse_sse(events[0])
        assert payload["content"] == "分析結果テキスト"

    def test_empty_text_returns_empty(self):
        events = chat_module._build_content_events("some-agent", "")
        assert events == []


class TestResolveBrochurePendingImages:
    """ブローシャ画像の不足補完テスト"""

    def test_fills_missing_slots_with_fallback_image(self):
        resolved = chat_module._resolve_brochure_pending_images({"hero": "data:image/png;base64,hero"})

        assert resolved["hero"] == "data:image/png;base64,hero"
        assert resolved["banner_instagram"] == resolved["banner_x"]
        assert resolved["banner_instagram"].startswith("data:image/svg+xml")

    def test_normalizes_banner_twitter_to_banner_x(self):
        resolved = chat_module._resolve_brochure_pending_images({"banner_twitter": "data:image/png;base64,x"})

        assert resolved["banner_x"] == "data:image/png;base64,x"


# --- _conversation_status_from_events テスト ---


class TestConversationStatusFromEvents:
    """_conversation_status_from_events のテスト"""

    def test_empty_events(self):
        assert chat_module._conversation_status_from_events([]) == "completed"

    def test_done_event(self):
        events = [{"event": "done"}]
        assert chat_module._conversation_status_from_events(events) == "completed"

    def test_approval_request_event(self):
        events = [{"event": "approval_request"}]
        assert chat_module._conversation_status_from_events(events) == "awaiting_approval"

    def test_manager_approval_request_event(self):
        events = [{"event": "approval_request", "data": {"approval_scope": "manager"}}]
        assert chat_module._conversation_status_from_events(events) == "awaiting_manager_approval"

    def test_error_event(self):
        events = [{"event": "error"}]
        assert chat_module._conversation_status_from_events(events) == "error"


def test_build_public_base_url_prefers_configured_setting(monkeypatch):
    """公開 URL は明示設定があればそれを優先する"""
    monkeypatch.setattr(chat_module, "get_settings", lambda: {"public_app_base_url": "https://app.example.com"})
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "method": "POST",
            "path": "/api/chat/conv/approve",
            "raw_path": b"/api/chat/conv/approve",
            "query_string": b"",
            "headers": [
                (b"host", b"internal.local"),
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-host", b"app.example.com"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("internal.local", 80),
            "root_path": "",
            "http_version": "1.1",
        }
    )

    assert chat_module._build_public_base_url(request) == "https://app.example.com"


def test_build_public_base_url_falls_back_to_request_base_url(monkeypatch):
    """公開 URL 設定が無ければ request.base_url を使う"""
    monkeypatch.setattr(chat_module, "get_settings", lambda: {"public_app_base_url": ""})
    request = Request(
        {
            "type": "http",
            "scheme": "http",
            "method": "POST",
            "path": "/api/chat/conv/approve",
            "raw_path": b"/api/chat/conv/approve",
            "query_string": b"",
            "headers": [
                (b"host", b"internal.local"),
                (b"x-forwarded-proto", b"https"),
                (b"x-forwarded-host", b"app.example.com"),
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("internal.local", 80),
            "root_path": "",
            "http_version": "1.1",
        }
    )

    assert chat_module._build_public_base_url(request) == "http://internal.local"

    def test_other_event_defaults_completed(self):
        events = [{"event": "text"}]
        assert chat_module._conversation_status_from_events(events) == "completed"


# --- _is_retryable_agent_error テスト ---


class TestIsRetryableAgentError:
    """_is_retryable_agent_error のテスト"""

    def test_rate_limit_is_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("429 Too Many Requests")) is True

    def test_timeout_is_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("Request timeout")) is True

    def test_context_length_not_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("context_length_exceeded")) is False

    def test_invalid_payload_not_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("invalid_payload error")) is False

    def test_generic_error_not_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("some unknown error")) is False

    def test_temporarily_is_retryable(self):
        assert chat_module._is_retryable_agent_error(Exception("temporarily unavailable")) is True


# --- _load_pending_approval_context テスト ---


class TestLoadPendingApprovalContext:
    """_load_pending_approval_context のテスト"""

    @pytest.mark.asyncio
    async def test_from_memory(self):
        """メモリにあるコンテキストを返す"""
        chat_module._pending_approvals["test-ctx"] = {
            "user_input": "テスト",
            "analysis_markdown": "分析",
            "plan_markdown": "企画",
            "model_settings": None,
        }
        result = await chat_module._load_pending_approval_context("test-ctx")
        assert result is not None
        assert result["user_input"] == "テスト"
        chat_module._pending_approvals.pop("test-ctx", None)

    @pytest.mark.asyncio
    async def test_not_found(self, monkeypatch):
        """メモリにもDBにもない場合は None"""
        chat_module._pending_approvals.clear()
        monkeypatch.setattr(
            "src.api.chat.get_conversation",
            lambda cid: None,
        )

        async def mock_get_conv(cid, owner_id: str | None = None, allow_cross_owner: bool = False):
            return None

        monkeypatch.setattr("src.api.chat.get_conversation", mock_get_conv)
        result = await chat_module._load_pending_approval_context("missing-ctx")
        assert result is None

    @pytest.mark.asyncio
    async def test_restores_model_settings_from_saved_approval_request(self, monkeypatch):
        """保存済み approval_request から model_settings を復元できる"""
        chat_module._pending_approvals.clear()

        async def mock_get_conv(_cid, owner_id: str | None = None, allow_cross_owner: bool = False):
            return {
                "status": "awaiting_approval",
                "input": "沖縄プラン",
                "messages": [
                    {
                        "event": "text",
                        "data": {"agent": "data-search-agent", "content": "分析結果"},
                    },
                    {
                        "event": "text",
                        "data": {"agent": "marketing-plan-agent", "content": "企画書本文"},
                    },
                    {
                        "event": "approval_request",
                        "data": {
                            "conversation_id": "saved-approval",
                            "plan_markdown": "企画書本文",
                            "model_settings": {"temperature": 0.4},
                        },
                    },
                ],
            }

        monkeypatch.setattr("src.api.chat.get_conversation", mock_get_conv)
        result = await chat_module._load_pending_approval_context("saved-approval")
        assert result is not None
        assert result["model_settings"] == {"temperature": 0.4}

    @pytest.mark.asyncio
    async def test_restores_manager_workflow_settings_from_saved_approval_request(self, monkeypatch):
        """保存済み manager approval_request から workflow_settings を復元できる"""
        chat_module._pending_approvals.clear()

        async def mock_get_conv(_cid, owner_id: str | None = None, allow_cross_owner: bool = False):
            return {
                "status": "awaiting_manager_approval",
                "input": "沖縄プラン",
                "metadata": {
                    "manager_approval_callback_token": "token-123",
                    "conversation_settings": {"work_iq_enabled": True, "source_scope": ["emails"]},
                    "work_iq_session": {
                        "enabled": True,
                        "source_scope": ["emails"],
                        "auth_mode": "delegated",
                        "owner_oid": "oid-123",
                        "owner_tid": "tid-123",
                        "owner_upn": "user@example.com",
                        "brief_summary": "要約",
                        "raw_excerpt": "should-not-persist",
                    },
                },
                "messages": [
                    {
                        "event": "text",
                        "data": {"agent": "data-search-agent", "content": "分析結果"},
                    },
                    {
                        "event": "text",
                        "data": {"agent": "plan-revision-agent", "content": "修正版企画書"},
                    },
                    {
                        "event": "approval_request",
                        "data": {
                            "conversation_id": "saved-manager-approval",
                            "plan_markdown": "修正版企画書",
                            "approval_scope": "manager",
                            "workflow_settings": {
                                "manager_approval_enabled": True,
                                "manager_email": "manager@example.com",
                            },
                        },
                    },
                ],
            }

        monkeypatch.setattr("src.api.chat.get_conversation", mock_get_conv)
        result = await chat_module._load_pending_approval_context("saved-manager-approval")
        assert result is not None
        assert result["approval_scope"] == "manager"
        assert result["manager_callback_token"] == "token-123"
        assert result["workflow_settings"] == {
            "manager_approval_enabled": True,
            "manager_email": "manager@example.com",
        }
        assert result["conversation_settings"] == {"work_iq_enabled": True, "source_scope": ["emails"]}
        assert result["work_iq_session"] == {
            "enabled": True,
            "source_scope": ["emails"],
            "auth_mode": "delegated",
            "owner_oid": "oid-123",
            "owner_tid": "tid-123",
            "owner_upn": "user@example.com",
            "brief_summary": "要約",
        }


def test_build_conversation_metadata_for_save_sanitizes_work_iq_session():
    """会話 metadata 保存時は raw Work IQ フィールドを除去する"""
    metadata = chat_module._build_conversation_metadata_for_save(
        "conv-workiq",
        existing_conversation=None,
        conversation_status="completed",
        conversation_settings={"work_iq_enabled": True, "source_scope": ["emails"]},
        work_iq_session={
            "enabled": True,
            "source_scope": ["emails"],
            "auth_mode": "delegated",
            "owner_oid": "oid-123",
            "owner_tid": "tid-123",
            "owner_upn": "user@example.com",
            "brief_summary": "安全な要約",
            "raw_excerpt": "should-not-persist",
        },
    )

    assert metadata is not None
    assert metadata["conversation_settings"] == {"work_iq_enabled": True, "source_scope": ["emails"]}
    assert metadata["work_iq_session"] == {
        "enabled": True,
        "source_scope": ["emails"],
        "auth_mode": "delegated",
        "owner_oid": "oid-123",
        "owner_tid": "tid-123",
        "owner_upn": "user@example.com",
        "brief_summary": "安全な要約",
    }


def test_build_work_iq_session_metadata_uses_preflight_status():
    """frontend preflight の auth status を新規 Work IQ session に反映する"""
    session = chat_module.build_work_iq_session_metadata(
        {"work_iq_enabled": True, "source_scope": ["emails"]},
        {
            "user_id": "anon-123",
            "auth_mode": "anonymous",
            "oid": "",
            "tid": "",
            "upn": "",
            "auth_error": "missing_token",
        },
        preflight_status="consent_required",
    )

    assert session["status"] == "consent_required"
    assert session["warning_code"] == "consent_required"


def test_build_work_iq_session_metadata_clears_stale_warning_for_delegated_identity():
    """delegated request では既存 session の stale warning を引き継がない"""
    session = chat_module.build_work_iq_session_metadata(
        {"work_iq_enabled": True, "source_scope": ["emails"]},
        {
            "user_id": "user-123",
            "auth_mode": "delegated",
            "oid": "oid-123",
            "tid": "tid-123",
            "upn": "user@example.com",
            "auth_error": "",
        },
        existing_session={
            "enabled": True,
            "source_scope": ["emails"],
            "auth_mode": "anonymous",
            "owner_oid": "",
            "owner_tid": "",
            "owner_upn": "",
            "warning_code": "auth_required",
            "status": "auth_required",
        },
        preflight_status="auth_required",
    )

    assert "status" not in session
    assert "warning_code" not in session
    assert session["auth_mode"] == "delegated"
    assert session["owner_oid"] == "oid-123"


def test_build_work_iq_session_metadata_allows_untrusted_identity_with_delegated_token():
    """署名検証境界外の bearer でも Work IQ delegated token があれば pre-block しない。"""
    session = chat_module.build_work_iq_session_metadata(
        {"work_iq_enabled": True, "source_scope": ["emails"]},
        {
            "user_id": "anon-123",
            "auth_mode": "anonymous",
            "oid": "",
            "tid": "",
            "upn": "",
            "auth_error": "untrusted_token",
        },
        existing_session={
            "enabled": True,
            "source_scope": ["emails"],
            "auth_mode": "anonymous",
            "owner_oid": "",
            "owner_tid": "",
            "owner_upn": "",
            "warning_code": "auth_required",
            "status": "auth_required",
        },
        preflight_status="auth_required",
        delegated_token_present=True,
    )

    assert "status" not in session
    assert "warning_code" not in session
    assert session["auth_mode"] == "anonymous"


@pytest.mark.asyncio
async def test_post_approval_events_falls_back_to_manual_manager_share(monkeypatch):
    """notification workflow 未設定でも manager approval URL を返して待機する"""

    lookup: dict[str, object] = {}

    monkeypatch.setattr(
        chat_module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test/project",
            "content_understanding_endpoint": "",
            "manager_approval_trigger_url": "",
            "logic_app_callback_url": "",
        },
    )

    async def fake_load_pending(_conversation_id: str, owner_id: str | None = None):
        return {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "旧企画書",
            "model_settings": {"temperature": 0.3},
            "workflow_settings": {
                "manager_approval_enabled": True,
                "manager_email": "manager@example.com",
            },
            "approval_scope": "user",
            "manager_callback_token": None,
            "owner_id": "",
        }

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        text = "規制チェック結果" if agent_name == "regulation-check-agent" else "修正版企画書"
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": text, "agent": agent_name},
                )
            ],
            "text": text,
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 10,
        }

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        lookup["owner_id"] = owner_id
        lookup["allow_cross_owner"] = allow_cross_owner
        return {"messages": []}

    monkeypatch.setattr(chat_module, "_load_pending_approval_context", fake_load_pending)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)

    events = [
        event async for event in chat_module._post_approval_events("承認", "conv-manual", "https://app.example.com")
    ]

    assert any("event: approval_request" in event for event in events)
    approval_event = next(event for event in events if "event: approval_request" in event)
    assert '"approval_scope": "manager"' in approval_event
    assert '"manager_delivery_mode": "manual"' in approval_event
    assert "manager_conversation_id=conv-manual" in approval_event
    assert "manager_approval_token=" in approval_event
    assert lookup["owner_id"] is None
    assert lookup["allow_cross_owner"] is False


# --- _extract_message_text テスト ---


class TestExtractMessageText:
    """_extract_message_text のテスト"""

    def test_no_contents(self):
        msg = MagicMock()
        msg.contents = None
        assert chat_module._extract_message_text(msg) == ""

    def test_empty_contents(self):
        msg = MagicMock()
        msg.contents = []
        assert chat_module._extract_message_text(msg) == ""

    def test_with_text_content(self):
        content = MagicMock()
        content.text = "Hello World"
        msg = MagicMock()
        msg.contents = [content]
        assert chat_module._extract_message_text(msg) == "Hello World"

    def test_non_string_text_skipped(self):
        content = MagicMock()
        content.text = 42
        msg = MagicMock()
        msg.contents = [content]
        assert chat_module._extract_message_text(msg) == ""

    def test_whitespace_only_skipped(self):
        content = MagicMock()
        content.text = "   "
        msg = MagicMock()
        msg.contents = [content]
        assert chat_module._extract_message_text(msg) == ""


class TestExtractToolNames:
    """実際のツール呼び出し推定のテスト"""

    def test_extracts_function_and_web_search_calls(self):
        function_call = MagicMock()
        function_call.type = "function_call"
        function_call.name = "generate_hero_image"

        web_search_call = MagicMock()
        web_search_call.type = "web_search_call"

        result = MagicMock()
        result.contents = None
        result.get_outputs.return_value = [function_call, web_search_call]

        assert chat_module._extract_tool_names(result, "brochure-gen-agent", "") == [
            "generate_hero_image",
            "web_search",
        ]

    def test_video_agent_falls_back_to_single_actual_tool(self):
        result = MagicMock()
        result.contents = None
        result.get_outputs.return_value = []

        assert chat_module._extract_tool_names(result, "video-gen-agent", "submitted") == ["generate_promo_video"]


# --- format_sse テスト ---


class TestFormatSSE:
    """format_sse のテスト"""

    def test_basic_format(self):
        result = chat_module.format_sse("text", {"content": "hello"})
        assert result.startswith("event: text\n")
        assert result.endswith("\n\n")

    def test_japanese_text(self):
        result = chat_module.format_sse("text", {"content": "日本語テスト"})
        assert "日本語テスト" in result

    def test_parseable_json(self):
        result = chat_module.format_sse("agent_progress", {"agent": "test", "step": 1})
        data_line = result.split("\n")[1]
        payload = json.loads(data_line.replace("data: ", ""))
        assert payload["agent"] == "test"


# --- 既存テスト ---


def test_extract_brochure_html_and_images() -> None:
    """Agent4 の HTML と埋め込み画像を抽出できる"""
    result_text = '```html\n<html><body><img src="data:image/png;base64,abc" alt="Hero image" /></body></html>\n```'

    html = chat_module._extract_brochure_html(result_text)
    assert html is not None
    assert html.startswith("<html>")

    images = chat_module._extract_inline_images(html)
    assert images == [{"url": "data:image/png;base64,abc", "alt": "Hero image"}]


def test_inject_images_into_html_replaces_banner_placeholders() -> None:
    """HTML 内のヒーロー画像とバナープレースホルダーを置換できる"""
    html = (
        "<html><body><main>"
        '<img src="HERO_IMAGE" alt="ヒーロー" />'
        '<img src="INSTAGRAM_BANNER_IMAGE" alt="Instagram" />'
        '<img src="X_BANNER_IMAGE" alt="X" />'
        "</main></body></html>"
    )

    result = chat_module._inject_images_into_html(
        html,
        {
            "hero": "data:image/png;base64,hero",
            "banner_instagram": "data:image/png;base64,instagram",
            "banner_x": "data:image/png;base64,xbanner",
        },
    )

    assert "HERO_IMAGE" not in result
    assert "INSTAGRAM_BANNER_IMAGE" not in result
    assert "X_BANNER_IMAGE" not in result
    assert "data:image/png;base64,hero" in result
    assert "data:image/png;base64,instagram" in result
    assert "data:image/png;base64,xbanner" in result


def test_inject_images_into_html_adds_platform_specific_banner_gallery() -> None:
    """プレースホルダーがない場合はプラットフォーム別バナーセクションを挿入する"""
    html = "<html><body><main><p>body</p></main><footer>footer</footer></body></html>"

    result = chat_module._inject_images_into_html(
        html,
        {
            "banner_instagram": "data:image/png;base64,instagram",
            "banner_x": "data:image/png;base64,xbanner",
        },
    )

    assert "Instagram 投稿用" in result
    assert "X 投稿用" in result
    assert "aspect-ratio:1 / 1" in result
    assert "aspect-ratio:1.91 / 1" in result
    assert result.index("SNS バナー") < result.index("<footer>")


@pytest.mark.asyncio
async def test_build_brochure_fallback_outcome_uses_static_fallback_banners(monkeypatch) -> None:
    """フォールバック販促物は外部画像 API を呼ばずに即時完了する。"""
    monkeypatch.setattr("src.agents.brochure_gen.set_current_conversation_id", lambda _conversation_id: None)
    monkeypatch.setattr("src.agents.brochure_gen.set_current_image_settings", lambda _settings: None)

    outcome = await chat_module._build_brochure_fallback_outcome(
        events=[],
        source_text="# 雪灯りのご褒美ステイ北海道",
        conversation_id="conv-brochure",
        step=5,
        total_steps=5,
        include_done=True,
        start_time=0.0,
    )

    assert outcome["tool_calls"] == 3
    assert "Instagram 投稿用" in outcome["text"]
    assert "X 投稿用" in outcome["text"]
    assert "Image unavailable" in urllib.parse.unquote(outcome["text"])
    image_events = [event for event in outcome["events"] if event.startswith("event: image")]
    assert len(image_events) == 3


@pytest.mark.asyncio
async def test_workflow_event_generator_creates_pending_approval(monkeypatch) -> None:
    """Azure 経路でも Agent2 の後に approval_request で停止する"""

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": f"{agent_name} output", "agent": agent_name},
                )
            ],
            "text": f"{agent_name} output",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    chat_module._pending_approvals.clear()

    events = [
        event async for event in chat_module.workflow_event_generator("沖縄プラン", "conv-azure", {"temperature": 0.2})
    ]
    parsed = [_parse_sse(event) for event in events]

    assert any(event_name == chat_module.SSEEventType.APPROVAL_REQUEST for event_name, _ in parsed)
    approval_payload = next(
        payload for event_name, payload in parsed if event_name == chat_module.SSEEventType.APPROVAL_REQUEST
    )
    assert approval_payload["model_settings"] == {"temperature": 0.2}

    assert chat_module._pending_approvals["conv-azure"]["analysis_markdown"] == "data-search-agent output"
    assert chat_module._pending_approvals["conv-azure"]["model_settings"] == {"temperature": 0.2}


@pytest.mark.asyncio
async def test_workflow_event_generator_injects_work_iq_brief_and_emits_tool_event(monkeypatch) -> None:
    """Work IQ brief を Agent2 prompt に注入し、sanitized tool_event を返す"""

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        if agent_name == "marketing-plan-agent":
            captured["marketing_prompt"] = user_input
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": f"{agent_name} output", "agent": agent_name},
                )
            ],
            "text": f"{agent_name} output",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
        }

    async def fake_generate_workplace_context_brief(**kwargs):
        captured["work_iq_args"] = kwargs
        return {
            "brief_summary": "メールでは家族向け訴求と春休み需要が重視されていました。",
            "brief_source_metadata": [{"source": "emails", "label": "メール", "count": 2}],
            "status": "completed",
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "generate_workplace_context_brief", fake_generate_workplace_context_brief)
    chat_module._pending_approvals.clear()

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "沖縄プラン",
            "conv-workiq",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": False, "manager_email": "", "work_iq_runtime": "graph_prefetch"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["emails"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["emails"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            work_iq_graph_access_token="graph-token",
            user_time_zone="Asia/Tokyo",
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert "Work IQ の職場コンテキスト" in str(captured["marketing_prompt"])
    assert "春休み需要" in str(captured["marketing_prompt"])
    assert captured["work_iq_args"] == {
        "user_input": "沖縄プラン",
        "source_scope": ["emails"],
        "access_token": "graph-token",
        "user_time_zone": "Asia/Tokyo",
    }
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("source") == "workiq"
        and payload.get("status") == "completed"
        for event_name, payload in parsed
    )
    assert chat_module._pending_approvals["conv-workiq"]["work_iq_session"]["brief_summary"] == (
        "メールでは家族向け訴求と春休み需要が重視されていました。"
    )


@pytest.mark.asyncio
async def test_workflow_event_generator_returns_error_when_analysis_is_insufficient(monkeypatch) -> None:
    """分析結果が不足している場合は marketing-plan を実行せず明示エラーを返す。"""

    calls: list[str] = []

    async def fake_execute_agent_with_runtime(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: dict | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
    ):
        del agent_step, user_input, conversation_id, model_settings, workflow_settings, work_iq_session, work_iq_access_token, total_steps
        calls.append(agent_name)
        if agent_name != "data-search-agent":
            raise AssertionError("marketing-plan-agent should not run with insufficient analysis")
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": "分析", "agent": "data-search-agent"},
                )
            ],
            "text": "分析",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(chat_module, "_execute_agent_with_runtime", fake_execute_agent_with_runtime)
    monkeypatch.setattr(chat_module, "_resolve_work_iq_runtime", lambda _settings: "graph_prefetch")

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "沖縄プラン",
            "conv-insufficient-analysis",
            {"temperature": 0.2},
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert calls == ["data-search-agent"]
    assert (
        chat_module.SSEEventType.ERROR,
        {
            "message": "Agent1 の分析結果が不足しているため、企画書を生成できません。データ検索結果を確認して再試行してください。",
            "code": "INSUFFICIENT_ANALYSIS_INPUT",
        },
    ) in parsed


@pytest.mark.asyncio
async def test_workflow_event_generator_blocks_foundry_tool_when_sign_in_is_required(monkeypatch) -> None:
    """Foundry Work IQ が auth_required の場合は marketing-plan を実行せず fail-closed にする。"""

    captured: dict[str, object] = {"fetch_called": False, "marketing_called": False}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: chat_module.WorkflowSettings | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, user_input, conversation_id, model_settings, workflow_settings, work_iq_session, work_iq_access_token, total_steps, include_done
        if agent_name == "marketing-plan-agent":
            captured["marketing_called"] = True
        return {
            "events": [],
            "text": f"{agent_name} output",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
        }

    async def fake_generate_workplace_context_brief(**kwargs):
        del kwargs
        captured["fetch_called"] = True
        return {
            "brief_summary": "should not run",
            "brief_source_metadata": [],
            "status": "completed",
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "generate_workplace_context_brief", fake_generate_workplace_context_brief)

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "沖縄プラン",
            "conv-workiq-auth",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": False, "manager_email": "", "work_iq_runtime": "foundry_tool"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["emails"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["emails"],
                "auth_mode": "anonymous",
                "owner_oid": "",
                "owner_tid": "",
                "owner_upn": "",
                "warning_code": "auth_required",
                "status": "auth_required",
            },
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert captured["fetch_called"] is False
    assert captured["marketing_called"] is False
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") == "workiq_foundry_tool"
        and payload.get("status") == "auth_required"
        and payload.get("agent") == "marketing-plan-agent"
        and payload.get("provider") == "foundry"
        and payload.get("display_name") == "Work IQ context tools"
        and payload.get("source_scope") == ["emails"]
        for event_name, payload in parsed
    )
    assert (
        chat_module.SSEEventType.ERROR,
        {
            "message": "Work IQ を使うにはサインインが必要です。サインイン後に再試行してください。",
            "code": "WORKIQ_AUTH_REQUIRED",
        },
    ) in parsed


@pytest.mark.asyncio
async def test_workflow_event_generator_keeps_foundry_failure_without_graph_prefetch_fallback(monkeypatch) -> None:
    """foundry_tool が marketing-plan で失敗しても graph_prefetch へ自動退避しない。"""

    captured: dict[str, object] = {"marketing_calls": []}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: chat_module.WorkflowSettings | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        if agent_name == "data-search-agent":
            return {
                "events": [],
                "text": "analysis output",
                "success": True,
                "latency_seconds": 0.1,
                "tool_calls": 1,
            }
        captured["marketing_calls"].append(
            {
                "prompt": user_input,
                "workflow_settings": dict(workflow_settings or {}),
                "access_token": work_iq_access_token,
            }
        )
        runtime = (workflow_settings or {}).get("work_iq_runtime")
        if runtime == "graph_prefetch":
            return {
                "events": [
                    chat_module.format_sse(
                        chat_module.SSEEventType.TEXT,
                        {"content": "# graph fallback plan", "agent": "marketing-plan-agent"},
                    )
                ],
                "text": "# graph fallback plan",
                "success": True,
                "latency_seconds": 0.1,
                "tool_calls": 1,
            }
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TOOL_EVENT,
                    {
                        "tool": "foundry_prompt_agent",
                        "status": "failed",
                        "agent": "marketing-plan-agent",
                        "error_code": "PROMPT_AGENT_RUNTIME_FAILED",
                        "error_message": "Error code: 500 - {'error': {'code': 'server_error'}}",
                    },
                )
            ]
            + [
                chat_module.format_sse(
                    chat_module.SSEEventType.ERROR,
                    {"message": "marketing-plan-agent failed", "code": "AGENT_RUNTIME_ERROR"},
                )
            ],
            "text": "",
            "success": False,
            "latency_seconds": 0.1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    chat_module._pending_approvals.clear()

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "夏のハワイ学生旅行向けプランを企画して",
            "conv-workiq-fallback",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": True, "manager_email": "manager@example.com", "work_iq_runtime": "foundry_tool"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["meeting_notes"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["meeting_notes"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            work_iq_access_token="foundry-token",
            work_iq_graph_access_token="graph-token",
            user_time_zone="Asia/Tokyo",
        )
    ]
    parsed = [_parse_sse(event) for event in events]
    tool_events = [payload for event_name, payload in parsed if event_name == chat_module.SSEEventType.TOOL_EVENT]

    marketing_calls = captured["marketing_calls"]
    assert isinstance(marketing_calls, list)
    assert len(marketing_calls) == 1
    assert marketing_calls[0]["workflow_settings"]["work_iq_runtime"] == "foundry_tool"
    assert [payload["tool"] for payload in tool_events] == [
        "workiq_foundry_tool",
        "foundry_prompt_agent",
    ]
    assert [payload["status"] for payload in tool_events] == [
        "running",
        "failed",
    ]
    assert not any(
        payload.get("tool") == "foundry_prompt_agent" and payload.get("status") == "completed"
        for payload in tool_events
    )
    assert any(event_name == chat_module.SSEEventType.ERROR and payload.get("code") == "AGENT_RUNTIME_ERROR" for event_name, payload in parsed)
    assert "conv-workiq-fallback" not in chat_module._pending_approvals


@pytest.mark.asyncio
async def test_workflow_event_generator_falls_back_to_graph_prefetch_on_foundry_work_iq_obo_failure(monkeypatch) -> None:
    """Foundry Work IQ の OBO 失敗時だけ graph_prefetch に退避する。"""

    captured: dict[str, object] = {"marketing_calls": []}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: chat_module.WorkflowSettings | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, conversation_id, model_settings, work_iq_session, total_steps, include_done
        if agent_name == "data-search-agent":
            return {
                "events": [],
                "text": "analysis output",
                "success": True,
                "latency_seconds": 0.1,
                "tool_calls": 1,
            }
        if agent_name == "marketing-plan-agent":
            captured["marketing_calls"].append(
                {
                    "prompt": user_input,
                    "workflow_settings": dict(workflow_settings or {}),
                    "access_token": work_iq_access_token,
                }
            )
            runtime = (workflow_settings or {}).get("work_iq_runtime")
            if runtime == "graph_prefetch":
                return {
                    "events": [
                        chat_module.format_sse(
                            chat_module.SSEEventType.TEXT,
                            {"content": "# graph fallback plan", "agent": "marketing-plan-agent"},
                        )
                    ],
                    "text": "# graph fallback plan",
                    "success": True,
                    "latency_seconds": 0.1,
                    "tool_calls": 1,
                }
            return {
                "events": [
                    chat_module.format_sse(
                        chat_module.SSEEventType.TOOL_EVENT,
                        {
                            "tool": "workiq_foundry_tool",
                            "status": "auth_required",
                            "agent": "marketing-plan-agent",
                            "error_code": "WORKIQ_OBO_TOKEN_FAILED",
                        },
                    ),
                    chat_module.format_sse(
                        chat_module.SSEEventType.ERROR,
                        {
                            "message": "Work IQ を使うにはサインインが必要です。サインイン後に再試行してください。",
                            "code": "WORKIQ_AUTH_REQUIRED",
                        },
                    ),
                ],
                "text": "",
                "success": False,
                "latency_seconds": 0.1,
                "tool_calls": 0,
            }
        raise AssertionError(f"unexpected agent: {agent_name}")

    async def fake_generate_workplace_context_brief(**kwargs):
        captured["work_iq_args"] = kwargs
        return {
            "brief_summary": "メールでは家族向け訴求を重視していました。",
            "brief_source_metadata": [{"source": "emails", "label": "メール", "count": 2}],
            "status": "completed",
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "generate_workplace_context_brief", fake_generate_workplace_context_brief)
    chat_module._pending_approvals.clear()

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "夏のハワイ学生旅行向けプランを企画して",
            "conv-workiq-obo-fallback",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": True, "manager_email": "manager@example.com", "work_iq_runtime": "foundry_tool"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["emails"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["emails"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            work_iq_access_token="foundry-token",
            work_iq_graph_access_token="graph-token",
            user_time_zone="Asia/Tokyo",
        )
    ]
    parsed = [_parse_sse(event) for event in events]
    tool_events = [payload for event_name, payload in parsed if event_name == chat_module.SSEEventType.TOOL_EVENT]
    marketing_calls = captured["marketing_calls"]

    assert isinstance(marketing_calls, list)
    assert len(marketing_calls) == 2
    assert marketing_calls[0]["workflow_settings"]["work_iq_runtime"] == "foundry_tool"
    assert marketing_calls[0]["access_token"] == "foundry-token"
    assert marketing_calls[1]["workflow_settings"]["work_iq_runtime"] == "graph_prefetch"
    assert marketing_calls[1]["access_token"] == ""
    assert "家族向け訴求" in str(marketing_calls[1]["prompt"])
    assert captured["work_iq_args"] == {
        "user_input": "夏のハワイ学生旅行向けプランを企画して",
        "source_scope": ["emails"],
        "access_token": "graph-token",
        "user_time_zone": "Asia/Tokyo",
    }
    assert any(
        payload.get("tool") == "workiq_foundry_tool"
        and payload.get("status") == "auth_required"
        and payload.get("error_code") == "WORKIQ_OBO_TOKEN_FAILED"
        for payload in tool_events
    )
    assert any(
        payload.get("tool") == "generate_workplace_context_brief"
        and payload.get("status") == "completed"
        and payload.get("provider") == "workiq"
        for payload in tool_events
    )
    assert any(event_name == chat_module.SSEEventType.APPROVAL_REQUEST for event_name, _payload in parsed)
    assert not any(event_name == chat_module.SSEEventType.ERROR for event_name, _payload in parsed)
    assert "conv-workiq-obo-fallback" in chat_module._pending_approvals


@pytest.mark.asyncio
async def test_workflow_event_generator_keeps_foundry_tool_failure_when_graph_token_missing(monkeypatch) -> None:
    """graph token が無い場合は foundry_tool failure をそのまま返す。"""

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: chat_module.WorkflowSettings | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, user_input, conversation_id, model_settings, workflow_settings, work_iq_session, work_iq_access_token, total_steps, include_done
        if agent_name == "data-search-agent":
            return {
                "events": [],
                "text": "analysis output",
                "success": True,
                "latency_seconds": 0.1,
                "tool_calls": 1,
            }
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TOOL_EVENT,
                    {
                        "tool": "foundry_prompt_agent",
                        "status": "failed",
                        "agent": "marketing-plan-agent",
                        "error_code": "PROMPT_AGENT_RUNTIME_FAILED",
                        "error_message": "Error code: 500 - {'error': {'code': 'server_error'}}",
                    },
                ),
                chat_module.format_sse(
                    chat_module.SSEEventType.ERROR,
                    {"message": "marketing-plan-agent failed", "code": "AGENT_RUNTIME_ERROR"},
                ),
            ],
            "text": "",
            "success": False,
            "latency_seconds": 0.1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    chat_module._pending_approvals.clear()

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "夏のハワイ学生旅行向けプランを企画して",
            "conv-workiq-no-graph-fallback",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": False, "manager_email": "", "work_iq_runtime": "foundry_tool"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["meeting_notes"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["meeting_notes"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            work_iq_access_token="foundry-token",
            work_iq_graph_access_token="",
            user_time_zone="Asia/Tokyo",
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert any(
        event_name == chat_module.SSEEventType.ERROR and payload.get("code") == "AGENT_RUNTIME_ERROR"
        for event_name, payload in parsed
    )
    assert "conv-workiq-no-graph-fallback" not in chat_module._pending_approvals


@pytest.mark.asyncio
async def test_workflow_event_generator_uses_foundry_work_iq_tool_event_semantics(monkeypatch) -> None:
    """foundry_tool 実行時は prefetch 名ではなく canonical Foundry Work IQ telemetry を流す。"""

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: chat_module.WorkflowSettings | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, user_input, conversation_id, model_settings, workflow_settings, work_iq_session, work_iq_access_token, total_steps, include_done
        return {
            "events": [],
            "text": "analysis output" if agent_name == "data-search-agent" else "plan output",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    chat_module._pending_approvals.clear()

    events = [
        event
        async for event in chat_module.workflow_event_generator(
            "夏の北海道プランを企画して",
            "conv-foundry-workiq-semantics",
            {"temperature": 0.2},
            workflow_settings={"manager_approval_enabled": False, "manager_email": "", "work_iq_runtime": "foundry_tool"},
            conversation_settings={"work_iq_enabled": True, "source_scope": ["meeting_notes"]},
            work_iq_session={
                "enabled": True,
                "source_scope": ["meeting_notes"],
                "auth_mode": "delegated",
                "owner_oid": "oid-123",
                "owner_tid": "tid-123",
                "owner_upn": "user@example.com",
            },
            work_iq_access_token="foundry-token",
        )
    ]
    parsed = [_parse_sse(event) for event in events]
    tool_events = [payload for event_name, payload in parsed if event_name == chat_module.SSEEventType.TOOL_EVENT]

    assert [payload["tool"] for payload in tool_events] == ["workiq_foundry_tool", "workiq_foundry_tool"]
    assert [payload["status"] for payload in tool_events] == ["running", "completed"]
    assert all(payload["provider"] == "foundry" for payload in tool_events)
    assert all(payload["display_name"] == "Work IQ context tools" for payload in tool_events)
    assert all(payload["source_scope"] == ["meeting_notes"] for payload in tool_events)
    assert not any(payload.get("tool") == "generate_workplace_context_brief" for payload in tool_events)


@pytest.mark.asyncio
async def test_refine_events_reuse_pending_plan_context(monkeypatch) -> None:
    """承認待ちの修正では元の分析・企画書を含めて Agent2 を再実行する"""
    chat_module._pending_approvals.clear()
    chat_module._pending_approvals["conv-pending"] = {
        "user_input": "春の沖縄ファミリープランを作成",
        "analysis_markdown": "分析結果",
        "plan_markdown": "現在の企画書",
        "model_settings": {"top_p": 0.9},
    }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        captured["agent_name"] = agent_name
        captured["user_input"] = user_input
        captured["model_settings"] = model_settings
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": "修正版企画書", "agent": agent_name},
                )
            ],
            "text": "修正版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    events = [event async for event in chat_module._refine_events("キャッチコピーをもっと爽やかに", "conv-pending")]
    parsed = [_parse_sse(event) for event in events]

    assert captured["agent_name"] == "marketing-plan-agent"
    assert "現在の企画書" in str(captured["user_input"])
    assert captured["model_settings"] == {"top_p": 0.9}
    assert any(event_name == chat_module.SSEEventType.APPROVAL_REQUEST for event_name, _ in parsed)


@pytest.mark.asyncio
async def test_refine_events_completed_conversation_ignores_foundry_tool_when_work_iq_not_reused(monkeypatch) -> None:
    """完了後の通常修正では Work IQ tool を再実行しない runtime に補正する。"""

    captured: dict[str, object] = {}

    async def fake_load_pending_approval_context(_conversation_id: str, owner_id: str | None = None):
        del owner_id
        return None

    async def fake_run_single_agent(
        agent_name: str,
        step: int,
        user_input: str,
        conversation_id: str,
        workflow_settings: dict | None = None,
        work_iq_access_token: str = "",
    ):
        del agent_name, step, user_input, conversation_id
        captured["workflow_settings"] = workflow_settings
        captured["work_iq_access_token"] = work_iq_access_token
        yield chat_module.format_sse(chat_module.SSEEventType.DONE, {"conversation_id": "conv-completed-refine"})

    monkeypatch.setattr(chat_module, "_load_pending_approval_context", fake_load_pending_approval_context)
    monkeypatch.setattr(chat_module, "_run_single_agent", fake_run_single_agent)
    monkeypatch.setattr(
        chat_module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test",
            "marketing_plan_runtime": "legacy",
            "work_iq_runtime": "foundry_tool",
        },
    )

    events = [
        event
        async for event in chat_module._refine_events(
            "キャッチコピーをもっと爽やかに",
            "conv-completed-refine",
            work_iq_access_token="delegated-token",
        )
    ]

    assert events
    assert captured["workflow_settings"] == {
        "manager_approval_enabled": False,
        "manager_email": "",
        "marketing_plan_runtime": "legacy",
        "work_iq_runtime": "graph_prefetch",
    }
    assert captured["work_iq_access_token"] == "delegated-token"


@pytest.mark.asyncio
async def test_refine_events_uses_mcp_brief_for_evaluation_feedback(monkeypatch) -> None:
    """品質評価の改善では MCP ブリーフを優先利用する"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "沖縄向け春休みプランを作って",
            "metadata": {"user_messages": ["沖縄向け春休みプランを作って", "ファミリー訴求を強めたい"]},
            "messages": [
                {"event": "text", "data": {"agent": "data-search-agent", "content": "分析結果"}},
                {"event": "text", "data": {"agent": "marketing-plan-agent", "content": "初稿企画書"}},
                {"event": "text", "data": {"agent": "plan-revision-agent", "content": "修正版企画書"}},
                {"event": "text", "data": {"agent": "regulation-check-agent", "content": "⚠ 最安値表現に注意"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"top_p": 0.9},
                        "workflow_settings": {"manager_approval_enabled": False, "manager_email": ""},
                    },
                },
                {
                    "event": "evaluation_result",
                    "data": {"result": {"builtin": {"relevance": {"score": 2, "reason": "具体性不足"}}}},
                },
            ],
        }

    mcp_capture: dict[str, object] = {}

    async def fake_generate_improvement_brief(**kwargs):
        mcp_capture.update(kwargs)
        return {
            "evaluation_summary": "優先課題 2 件を検出しました。",
            "improvement_brief": "差別化の根拠と注意書きを強化してください。",
            "priority_issues": [
                {
                    "label": "関連性",
                    "reason": "スコア 2.0/5。具体性不足",
                    "suggested_action": "ターゲットに刺さる便益を明確にする",
                }
            ],
            "must_keep": ["タイトル: 春の沖縄ファミリー旅"],
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        captured["agent_name"] = agent_name
        captured["user_input"] = user_input
        captured["model_settings"] = model_settings
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": "改善版企画書", "agent": agent_name},
                )
            ],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 50,
        }

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: True)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    events = [
        event
        async for event in chat_module._refine_events(
            "以下の品質評価結果に基づいて企画書を改善してください:\n- relevance が低い",
            "conv-eval-mcp",
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert mcp_capture["plan_markdown"] == "修正版企画書"
    assert mcp_capture["regulation_summary"] == "⚠ 最安値表現に注意"
    assert mcp_capture["rejection_history"] == ["ファミリー訴求を強めたい"]
    assert captured["agent_name"] == "marketing-plan-agent"
    assert "## 改善ブリーフ" in str(captured["user_input"])
    assert "## 維持すべき要素" in str(captured["user_input"])
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") == "generate_improvement_brief"
        and payload.get("source") == "mcp"
        for event_name, payload in parsed
    )
    assert any(event_name == chat_module.SSEEventType.APPROVAL_REQUEST for event_name, _ in parsed)


@pytest.mark.asyncio
async def test_refine_events_falls_back_when_mcp_brief_unavailable(monkeypatch) -> None:
    """MCP が未使用でも従来の改善フローを維持する"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "北海道プランを作って",
            "messages": [
                {"event": "text", "data": {"agent": "plan-revision-agent", "content": "現在の企画書"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"temperature": 0.2},
                        "workflow_settings": {"manager_approval_enabled": False, "manager_email": ""},
                    },
                },
                {"event": "evaluation_result", "data": {"result": {"builtin": {}}}},
            ],
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        captured["user_input"] = user_input
        return {
            "events": [],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 20,
        }

    async def fake_generate_improvement_brief(**kwargs):
        return None

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: False)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    events = [
        event
        async for event in chat_module._refine_events(
            "品質評価を踏まえて改善してください",
            "conv-eval-fallback",
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert "## 改善ブリーフ" not in str(captured["user_input"])
    assert "品質評価を踏まえて改善してください" in str(captured["user_input"])
    assert not any(
        event_name == chat_module.SSEEventType.TOOL_EVENT and payload.get("tool") == "generate_improvement_brief"
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_refine_events_emits_failed_tool_event_when_mcp_falls_back(monkeypatch) -> None:
    """MCP が設定済みで失敗した場合は fallback を明示する"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "北海道プランを作って",
            "messages": [
                {"event": "text", "data": {"agent": "plan-revision-agent", "content": "現在の企画書"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"temperature": 0.2},
                        "workflow_settings": {"manager_approval_enabled": False, "manager_email": ""},
                    },
                },
                {"event": "evaluation_result", "data": {"result": {"builtin": {}}}},
            ],
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        captured["user_input"] = user_input
        return {
            "events": [],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 20,
        }

    async def fake_generate_improvement_brief(**kwargs):
        return None

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: True)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    events = [
        event
        async for event in chat_module._refine_events(
            "品質評価を踏まえて改善してください",
            "conv-eval-failed-mcp",
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert "品質評価を踏まえて改善してください" in str(captured["user_input"])
    assert any(
        event_name == chat_module.SSEEventType.TOOL_EVENT
        and payload.get("tool") == "generate_improvement_brief"
        and payload.get("status") == "failed"
        and payload.get("source") == "mcp"
        and payload.get("fallback") == "legacy_prompt"
        for event_name, payload in parsed
    )


@pytest.mark.asyncio
async def test_refine_events_evaluation_reuses_saved_work_iq_brief(monkeypatch) -> None:
    """評価ベースの改善でも保存済み Work IQ brief を prompt に再利用する"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "北海道の春プランを作って",
            "metadata": {
                "work_iq_session": {
                    "enabled": True,
                    "source_scope": ["emails"],
                    "auth_mode": "delegated",
                    "owner_oid": "oid-123",
                    "owner_tid": "tid-123",
                    "owner_upn": "user@example.com",
                    "brief_summary": "メールでは上質感を重視し、値引き訴求は弱める方針でした。",
                }
            },
            "messages": [
                {"event": "text", "data": {"agent": "marketing-plan-agent", "content": "現在の企画書"}},
                {"event": "text", "data": {"agent": "data-search-agent", "content": "分析結果"}},
                {"event": "text", "data": {"agent": "regulation-check-agent", "content": "規制結果"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"temperature": 0.2},
                        "workflow_settings": {"manager_approval_enabled": False, "manager_email": ""},
                    },
                },
                {"event": "evaluation_result", "data": {"result": {"builtin": {}}}},
            ],
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        captured["user_input"] = user_input
        return {
            "events": [],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 20,
        }

    async def fake_generate_improvement_brief(**kwargs):
        return None

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: False)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "_extract_user_message_history", lambda conversation: ["北海道の春プランを作って"])

    _ = [
        event
        async for event in chat_module._refine_events(
            "品質評価を踏まえて改善してください",
            "conv-eval-workiq",
            chat_module.RefineContext(source="evaluation"),
            work_iq_access_token="delegated-token",
        )
    ]

    assert "Work IQ の職場コンテキスト" in str(captured["user_input"])
    assert "値引き訴求は弱める方針" in str(captured["user_input"])


@pytest.mark.asyncio
async def test_refine_events_evaluation_uses_legacy_runtime_without_work_iq_tool(monkeypatch) -> None:
    """評価ベースの改善再実行では Work IQ tool を再実行しない"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "北海道の春プランを作って",
            "messages": [
                {"event": "text", "data": {"agent": "marketing-plan-agent", "content": "現在の企画書"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"temperature": 0.2},
                        "workflow_settings": {
                            "manager_approval_enabled": False,
                            "manager_email": "",
                            "marketing_plan_runtime": "foundry_preprovisioned",
                            "work_iq_runtime": "foundry_tool",
                        },
                    },
                },
                {"event": "evaluation_result", "data": {"result": {"builtin": {}}}},
            ],
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: dict | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_name, agent_step, user_input, conversation_id, model_settings, total_steps, include_done
        captured["workflow_settings"] = workflow_settings
        captured["work_iq_session"] = work_iq_session
        captured["work_iq_access_token"] = work_iq_access_token
        return {
            "events": [],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 20,
        }

    async def fake_generate_improvement_brief(**kwargs):
        return None

    supplied_work_iq_session = {"enabled": True, "source_scope": ["emails"], "auth_mode": "delegated"}
    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: False)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    _ = [
        event
        async for event in chat_module._refine_events(
            "品質評価を踏まえて改善してください",
            "conv-eval-workiq-token",
            chat_module.RefineContext(source="evaluation"),
            work_iq_session=supplied_work_iq_session,
            work_iq_access_token="delegated-token",
        )
    ]

    assert captured["work_iq_session"] is None
    assert captured["work_iq_access_token"] == ""
    assert captured["workflow_settings"] == {
        "manager_approval_enabled": False,
        "manager_email": "",
        "marketing_plan_runtime": "legacy",
        "work_iq_runtime": "graph_prefetch",
    }


@pytest.mark.asyncio
async def test_refine_events_evaluation_does_not_require_work_iq_token(monkeypatch) -> None:
    """評価ベース改善では既存企画書を使うため Work IQ token を要求しない"""

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        return {
            "input": "北海道の春プランを作って",
            "messages": [
                {"event": "text", "data": {"agent": "marketing-plan-agent", "content": "現在の企画書"}},
                {
                    "event": "approval_request",
                    "data": {
                        "model_settings": {"temperature": 0.2},
                        "workflow_settings": {
                            "manager_approval_enabled": False,
                            "manager_email": "",
                            "marketing_plan_runtime": "foundry_preprovisioned",
                            "work_iq_runtime": "foundry_tool",
                        },
                    },
                },
                {"event": "evaluation_result", "data": {"result": {"builtin": {}}}},
            ],
            "metadata": {
                "work_iq_session": {"enabled": True, "source_scope": ["emails"], "auth_mode": "delegated"}
            },
        }

    captured: dict[str, object] = {}

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: dict | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_name, agent_step, conversation_id, model_settings, total_steps, include_done
        captured["user_input"] = user_input
        captured["workflow_settings"] = workflow_settings
        captured["work_iq_session"] = work_iq_session
        captured["work_iq_access_token"] = work_iq_access_token
        return {
            "events": [],
            "text": "改善版企画書",
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 20,
        }

    async def fake_generate_improvement_brief(**kwargs):
        return None

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "is_improvement_mcp_configured", lambda: False)
    monkeypatch.setattr(chat_module, "generate_improvement_brief", fake_generate_improvement_brief)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)

    events = [
        event
        async for event in chat_module._refine_events(
            "品質評価を踏まえて改善してください",
            "conv-eval-workiq-no-token",
            chat_module.RefineContext(source="evaluation"),
        )
    ]
    parsed = [_parse_sse(event) for event in events]

    assert captured["work_iq_session"] is None
    assert captured["work_iq_access_token"] == ""
    assert captured["workflow_settings"] == {
        "manager_approval_enabled": False,
        "manager_email": "",
        "marketing_plan_runtime": "legacy",
        "work_iq_runtime": "graph_prefetch",
    }
    assert "Work IQ tool を再実行せず" in str(captured["user_input"])
    assert not any(event_name == chat_module.SSEEventType.ERROR for event_name, _ in parsed)
    assert any(event_name == chat_module.SSEEventType.APPROVAL_REQUEST for event_name, _ in parsed)


@pytest.mark.asyncio
async def test_post_approval_uses_revised_plan_for_review_and_logic_app(monkeypatch) -> None:
    """承認後の品質レビューと Logic Apps 連携は修正版企画書を使う"""

    monkeypatch.setattr(
        chat_module,
        "get_settings",
        lambda: {"project_endpoint": "https://example.test/project", "content_understanding_endpoint": ""},
    )

    async def fake_load_pending(_conversation_id, owner_id: str | None = None):
        return {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "旧企画書",
            "model_settings": {"temperature": 0.3},
        }

    review_inputs: list[str] = []
    logic_app_calls: list[dict[str, str]] = []
    popped_video_conversation_ids: list[str] = []

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        payload_by_agent = {
            "regulation-check-agent": "規制チェック結果",
            "plan-revision-agent": "修正版企画書",
            "brochure-gen-agent": "```html\n<html><body>brochure</body></html>\n```",
            "video-gen-agent": '{"status": "submitted"}',
        }
        text = payload_by_agent[agent_name]
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": text, "agent": agent_name},
                )
            ],
            "text": text,
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 10,
        }

    async def fake_quality_review(review_input: str):
        review_inputs.append(review_input)
        return []

    async def fake_trigger_logic_app(conversation_id: str, plan_markdown: str, brochure_html: str):
        logic_app_calls.append(
            {
                "conversation_id": conversation_id,
                "plan_markdown": plan_markdown,
                "brochure_html": brochure_html,
            }
        )

    def fake_pop_pending_video_job(conversation_id: str):
        popped_video_conversation_ids.append(conversation_id)
        return None

    async def fake_poll_video_job(job_id: str, max_wait: int = 120):
        return None

    monkeypatch.setattr(chat_module, "_load_pending_approval_context", fake_load_pending)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "_maybe_run_quality_review", fake_quality_review)
    monkeypatch.setattr(chat_module, "_trigger_logic_app", fake_trigger_logic_app)
    monkeypatch.setattr("src.agents.video_gen.pop_pending_video_job", fake_pop_pending_video_job)
    monkeypatch.setattr("src.agents.video_gen.poll_video_job", fake_poll_video_job)

    events = [event async for event in chat_module._post_approval_events("承認", "conv-revised")]
    assert events
    assert review_inputs and "修正版企画書" in review_inputs[0]
    assert "旧企画書" not in review_inputs[0]
    assert logic_app_calls == [
        {
            "conversation_id": "conv-revised",
            "plan_markdown": "修正版企画書",
            "brochure_html": "<html><body>brochure</body></html>",
        }
    ]
    assert popped_video_conversation_ids == ["conv-revised"]


def test_build_video_poll_completion_events_returns_timeout_message_for_missing_video() -> None:
    """動画 URL を回収できない場合は明示的な warning メッセージを返す"""

    events = chat_module._build_video_poll_completion_events(None, background_update=True)

    assert events == [
        {
            "event": "text",
            "data": {
                "content": "⚠️ アバター動画の生成完了を確認できませんでした。Photo Avatar ジョブがタイムアウトまたは失敗した可能性があります。",
                "agent": "video-gen-agent",
                "content_type": "text",
                "background_update": True,
            },
        }
    ]


def test_build_video_poll_completion_events_returns_failure_detail() -> None:
    """動画ジョブ失敗時は timeout ではなく失敗詳細を返す"""

    events = chat_module._build_video_poll_completion_events(
        {
            "status": "failed",
            "video_url": None,
            "message": "Unsupported gesture for lisa/casual-sitting.",
        },
        background_update=True,
    )

    assert events == [
        {
            "event": "text",
            "data": {
                "content": "⚠️ アバター動画の生成に失敗しました。 Unsupported gesture for lisa/casual-sitting.",
                "agent": "video-gen-agent",
                "content_type": "text",
                "background_update": True,
            },
        }
    ]


@pytest.mark.asyncio
async def test_post_approval_emits_video_timeout_message_when_polling_times_out(monkeypatch) -> None:
    """動画 polling が完了しない場合でも、永続化用の warning を SSE に流す"""

    monkeypatch.setattr(
        chat_module,
        "get_settings",
        lambda: {"project_endpoint": "https://example.test/project", "content_understanding_endpoint": ""},
    )

    async def fake_load_pending(_conversation_id, owner_id: str | None = None):
        return {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "旧企画書",
            "model_settings": {"temperature": 0.3},
        }

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, user_input, conversation_id, model_settings, total_steps, include_done
        payload_by_agent = {
            "regulation-check-agent": "規制チェック結果",
            "plan-revision-agent": "修正版企画書",
            "brochure-gen-agent": "```html\n<html><body>brochure</body></html>\n```",
            "video-gen-agent": '{"status": "submitted", "message": "🎬 動画生成ジョブを送信しました"}',
        }
        text = payload_by_agent[agent_name]
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": text, "agent": agent_name},
                )
            ],
            "text": text,
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 10,
        }

    poll_waits: list[int] = []

    async def fake_poll_video_job(job_id: str, max_wait: int = 0):
        assert job_id == "video-job-123"
        poll_waits.append(max_wait)
        return None

    async def fake_quality_review(review_input: str):
        assert "修正版企画書" in review_input
        return []

    async def fake_trigger_logic_app(conversation_id: str, plan_markdown: str, brochure_html: str):
        assert conversation_id == "conv-video-timeout"
        assert plan_markdown == "修正版企画書"
        assert brochure_html == "<html><body>brochure</body></html>"

    def fake_pop_pending_video_job(conversation_id: str):
        assert conversation_id == "conv-video-timeout"
        return {"job_id": "video-job-123", "status": "submitted"}

    monkeypatch.setattr(chat_module, "_load_pending_approval_context", fake_load_pending)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "_maybe_run_quality_review", fake_quality_review)
    monkeypatch.setattr(chat_module, "_trigger_logic_app", fake_trigger_logic_app)
    monkeypatch.setattr("src.agents.video_gen.pop_pending_video_job", fake_pop_pending_video_job)
    monkeypatch.setattr("src.agents.video_gen.poll_video_job", fake_poll_video_job)

    parsed_events = [
        _parse_sse(event)
        for event in [event async for event in chat_module._post_approval_events("承認", "conv-video-timeout")]
    ]

    assert poll_waits == [420]
    assert (
        "text",
        {
            "content": "⚠️ アバター動画の生成完了を確認できませんでした。Photo Avatar ジョブがタイムアウトまたは失敗した可能性があります。",
            "agent": "video-gen-agent",
            "content_type": "text",
        },
    ) in parsed_events


@pytest.mark.asyncio
async def test_post_approval_does_not_block_done_when_video_agent_submission_hangs(monkeypatch) -> None:
    """video agent のジョブ送信が戻らない場合でも SSE は done まで進む。"""

    monkeypatch.setattr(
        chat_module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test/project",
            "content_understanding_endpoint": "",
            "logic_app_callback_url": "",
        },
    )
    monkeypatch.setattr(chat_module, "_VIDEO_AGENT_SUBMISSION_MAX_WAIT_SECONDS", 0.01)

    async def fake_load_pending(_conversation_id, owner_id: str | None = None):
        return {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "旧企画書",
            "model_settings": {"temperature": 0.3},
        }

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        workflow_settings: dict | None = None,
        work_iq_session: dict | None = None,
        work_iq_access_token: str = "",
        total_steps: int = 5,
        include_done: bool = False,
    ):
        del agent_step, user_input, conversation_id, model_settings, workflow_settings, work_iq_session
        del work_iq_access_token, total_steps, include_done
        if agent_name == "video-gen-agent":
            await asyncio.sleep(1)

        payload_by_agent = {
            "regulation-check-agent": "規制チェック結果",
            "plan-revision-agent": "修正版企画書",
            "brochure-gen-agent": "```html\n<html><body>brochure</body></html>\n```",
            "video-gen-agent": '{"status": "submitted"}',
        }
        text = payload_by_agent[agent_name]
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.TEXT,
                    {"content": text, "agent": agent_name},
                )
            ],
            "text": text,
            "success": True,
            "latency_seconds": 0.1,
            "tool_calls": 1,
            "total_tokens": 10,
        }

    async def fake_quality_review(review_input: str):
        assert "修正版企画書" in review_input
        return []

    async def fake_trigger_logic_app(conversation_id: str, plan_markdown: str, brochure_html: str):
        assert conversation_id == "conv-video-submission-timeout"
        assert plan_markdown == "修正版企画書"
        assert brochure_html == "<html><body>brochure</body></html>"

    monkeypatch.setattr(chat_module, "_load_pending_approval_context", fake_load_pending)
    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    monkeypatch.setattr(chat_module, "_maybe_run_quality_review", fake_quality_review)
    monkeypatch.setattr(chat_module, "_trigger_logic_app", fake_trigger_logic_app)
    monkeypatch.setattr("src.agents.video_gen.pop_pending_video_job", lambda _conversation_id: None)

    parsed_events = [
        _parse_sse(event)
        for event in [
            event async for event in chat_module._post_approval_events("承認", "conv-video-submission-timeout")
        ]
    ]

    assert (
        "text",
        {
            "content": chat_module._VIDEO_SUBMISSION_TIMEOUT_MESSAGE,
            "agent": "video-gen-agent",
            "content_type": "text",
        },
    ) in parsed_events
    assert any(event_name == chat_module.SSEEventType.DONE for event_name, _ in parsed_events)


@pytest.mark.asyncio
async def test_append_post_completion_updates_does_not_enable_cross_owner_for_empty_owner_id(monkeypatch) -> None:
    """空の owner_id が入っていても background update で cross-owner を有効化しない"""

    lookup: dict[str, object] = {}

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        lookup["owner_id"] = owner_id
        lookup["allow_cross_owner"] = allow_cross_owner
        return {
            "input": "沖縄プラン",
            "messages": [],
            "status": "completed",
            "user_id": "user-123",
        }

    async def fake_trigger_logic_app(conversation_id: str, plan_markdown: str, brochure_html: str):
        assert conversation_id == "conv-owner"
        assert plan_markdown == "修正版企画書"
        assert brochure_html == "<html></html>"

    async def fake_append_conversation_events(**kwargs):
        lookup["saved_owner_id"] = kwargs["owner_id"]

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "_trigger_logic_app", fake_trigger_logic_app)
    monkeypatch.setattr(chat_module, "append_conversation_events", fake_append_conversation_events)

    await chat_module._append_post_completion_updates(
        "conv-owner",
        {
            "conversation_id": "conv-owner",
            "review_input": "",
            "revised_plan_markdown": "修正版企画書",
            "brochure_html": "<html></html>",
            "video_job_id": None,
            "owner_id": "",
        },
    )

    assert lookup["owner_id"] is None
    assert lookup["allow_cross_owner"] is False
    assert lookup["saved_owner_id"] == "user-123"


@pytest.mark.asyncio
async def test_append_post_completion_updates_safe_does_not_enable_cross_owner_for_empty_owner_id(monkeypatch) -> None:
    """空の owner_id が入っていても recovery lookup で cross-owner を有効化しない"""

    lookup: dict[str, object] = {}

    async def fake_append_post_completion_updates(
        conversation_id: str,
        update_context: chat_module.PostCompletionUpdateContext,
    ) -> None:
        del conversation_id, update_context
        raise RuntimeError("boom")

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        lookup["owner_id"] = owner_id
        lookup["allow_cross_owner"] = allow_cross_owner
        return {
            "input": "沖縄プラン",
            "messages": [],
            "status": "completed",
            "user_id": "user-123",
        }

    async def fake_append_conversation_events(**kwargs):
        lookup["saved_owner_id"] = kwargs["owner_id"]

    monkeypatch.setattr(chat_module, "_append_post_completion_updates", fake_append_post_completion_updates)
    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "append_conversation_events", fake_append_conversation_events)

    await chat_module._append_post_completion_updates_safe(
        "conv-owner",
        {
            "conversation_id": "conv-owner",
            "review_input": "",
            "revised_plan_markdown": "修正版企画書",
            "brochure_html": "<html></html>",
            "video_job_id": None,
            "owner_id": "",
        },
    )

    assert lookup["owner_id"] is None
    assert lookup["allow_cross_owner"] is False
    assert lookup["saved_owner_id"] == "user-123"


@pytest.mark.asyncio
async def test_run_manager_approval_continuation_does_not_enable_cross_owner_for_empty_owner_id(monkeypatch) -> None:
    """空の owner_id が入っていても manager continuation で cross-owner を有効化しない"""

    lookup: dict[str, object] = {}

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        lookup["owner_id"] = owner_id
        lookup["allow_cross_owner"] = allow_cross_owner
        return {
            "input": "沖縄プラン",
            "messages": [],
            "status": "completed",
            "user_id": "user-123",
        }

    async def fake_post_approval_events(
        _response: str,
        _conversation_id: str,
        approval_context=None,
        owner_id: str | None = None,
        register_background_job=None,
    ):
        del approval_context, owner_id, register_background_job
        if False:
            yield ""
        return

    async def fake_append_conversation_events(**kwargs):
        lookup["saved_owner_id"] = kwargs["owner_id"]

    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "_post_approval_events", fake_post_approval_events)
    monkeypatch.setattr(chat_module, "append_conversation_events", fake_append_conversation_events)

    await chat_module._run_manager_approval_continuation(
        "conv-owner",
        {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "修正版企画書",
            "model_settings": {"temperature": 0.3},
            "workflow_settings": {"manager_approval_enabled": True, "manager_email": "manager@example.com"},
            "approval_scope": "manager",
            "manager_callback_token": "token-123",
            "owner_id": "",
        },
    )

    assert lookup["owner_id"] is None
    assert lookup["allow_cross_owner"] is False
    assert lookup["saved_owner_id"] == "user-123"


@pytest.mark.asyncio
async def test_continue_after_manager_approval_safe_does_not_enable_cross_owner_for_empty_owner_id(monkeypatch) -> None:
    """空の owner_id が入っていても manager continuation の recovery lookup で cross-owner を有効化しない"""

    lookup: dict[str, object] = {}

    async def fake_run_manager_approval_continuation(
        conversation_id: str,
        approval_context=None,
    ) -> None:
        del conversation_id, approval_context
        raise RuntimeError("boom")

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ):
        lookup["owner_id"] = owner_id
        lookup["allow_cross_owner"] = allow_cross_owner
        return {
            "input": "沖縄プラン",
            "messages": [],
            "status": "completed",
            "user_id": "user-123",
        }

    async def fake_append_conversation_events(**kwargs):
        lookup["saved_owner_id"] = kwargs["owner_id"]

    monkeypatch.setattr(chat_module, "_run_manager_approval_continuation", fake_run_manager_approval_continuation)
    monkeypatch.setattr(chat_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(chat_module, "append_conversation_events", fake_append_conversation_events)

    await chat_module._continue_after_manager_approval_safe(
        "conv-owner",
        {
            "user_input": "沖縄プラン",
            "analysis_markdown": "分析結果",
            "plan_markdown": "修正版企画書",
            "model_settings": {"temperature": 0.3},
            "workflow_settings": {"manager_approval_enabled": True, "manager_email": "manager@example.com"},
            "approval_scope": "manager",
            "manager_callback_token": "token-123",
            "owner_id": "",
        },
    )

    assert lookup["owner_id"] is None
    assert lookup["allow_cross_owner"] is False
    assert lookup["saved_owner_id"] == "user-123"


# --- _get_reference_brochure_path テスト ---


class TestGetReferenceBrochurePath:
    """_get_reference_brochure_path のテスト"""

    def test_no_endpoint_returns_none(self, monkeypatch):
        """CONTENT_UNDERSTANDING_ENDPOINT 未設定時は None"""
        monkeypatch.delenv("CONTENT_UNDERSTANDING_ENDPOINT", raising=False)
        result = chat_module._get_reference_brochure_path()
        assert result is None

    def test_with_endpoint_but_no_file(self, monkeypatch):
        """エンドポイントがあっても data/*.pdf の最新ファイルは暗黙参照しない。"""
        monkeypatch.setenv("CONTENT_UNDERSTANDING_ENDPOINT", "https://test.cognitiveservices.azure.com")
        result = chat_module._get_reference_brochure_path()
        assert result is None


# --- _maybe_run_quality_review テスト ---


class TestMaybeRunQualityReview:
    """_maybe_run_quality_review のテスト"""

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty(self):
        """空文字列では空リストを返す"""
        result = await chat_module._maybe_run_quality_review("")
        assert result == []

    @pytest.mark.asyncio
    async def test_whitespace_input_returns_empty(self):
        result = await chat_module._maybe_run_quality_review("   ")
        assert result == []

    @pytest.mark.asyncio
    async def test_review_agent_none_returns_empty(self, monkeypatch):
        """レビューエージェントが None の場合は空リスト"""

        def mock_create():
            return None

        monkeypatch.setattr("src.agents.create_review_agent", mock_create)
        result = await chat_module._maybe_run_quality_review("some content")
        assert result == []

    @pytest.mark.asyncio
    async def test_review_agent_exception_returns_empty(self, monkeypatch):
        """レビューエージェントが例外を投げても空リスト"""

        def mock_create():
            raise RuntimeError("fail")

        monkeypatch.setattr("src.agents.create_review_agent", mock_create)
        result = await chat_module._maybe_run_quality_review("content")
        assert result == []


# --- _trigger_logic_app テスト ---


class TestTriggerLogicApp:
    """_trigger_logic_app のテスト"""

    @pytest.mark.asyncio
    async def test_no_callback_url_skips(self, monkeypatch):
        """LOGIC_APP_CALLBACK_URL 未設定時はスキップ"""
        monkeypatch.delenv("LOGIC_APP_CALLBACK_URL", raising=False)
        await chat_module._trigger_logic_app("conv-1", "# Plan", "<html>Brochure</html>")

    @pytest.mark.asyncio
    async def test_with_callback_url_network_error(self, monkeypatch):
        """ネットワークエラーでも例外をスローしない（非致命的）"""
        monkeypatch.setenv("LOGIC_APP_CALLBACK_URL", "https://example.com/webhook")

        import urllib.request
        from unittest.mock import patch

        with patch.object(urllib.request, "urlopen", side_effect=OSError("Connection refused")):
            await chat_module._trigger_logic_app("conv-1", "# Plan", "<html></html>")


# --- ChatRequest / ApproveRequest バリデーションテスト ---


class TestRequestValidation:
    """リクエストモデルのバリデーション"""

    def test_chat_request_strips_whitespace(self):
        req = chat_module.ChatRequest(message="  hello  ")
        assert req.message == "hello"

    def test_chat_request_rejects_empty(self):
        with pytest.raises(Exception):
            chat_module.ChatRequest(message="")

    def test_approve_request_strips_whitespace(self):
        req = chat_module.ApproveRequest(conversation_id="conv-1", response="  承認  ")
        assert req.response == "承認"


# --- workflow_event_generator 失敗シナリオ ---


@pytest.mark.asyncio
async def test_workflow_event_generator_agent1_failure(monkeypatch) -> None:
    """Agent1 が失敗した場合にジェネレータが停止すること"""

    async def fake_execute_agent(
        agent_name: str,
        agent_step: int,
        user_input: str,
        conversation_id: str,
        model_settings: dict | None = None,
        total_steps: int = 5,
        include_done: bool = False,
    ):
        return {
            "events": [
                chat_module.format_sse(
                    chat_module.SSEEventType.ERROR,
                    {"message": "Agent1 failed", "code": "AGENT_RUNTIME_ERROR"},
                )
            ],
            "text": "",
            "success": False,
            "latency_seconds": 0.1,
            "tool_calls": 0,
        }

    monkeypatch.setattr(chat_module, "_execute_agent", fake_execute_agent)
    chat_module._pending_approvals.clear()

    events = [event async for event in chat_module.workflow_event_generator("テスト", "conv-fail", None)]
    parsed = [_parse_sse(event) for event in events]

    assert any(event_name == "error" for event_name, _ in parsed)
    assert not any(event_name == "approval_request" for event_name, _ in parsed)
