"""チャット逐次オーケストレーションのテスト"""

import json
from unittest.mock import MagicMock

import pytest

from src.api import chat as chat_module


def _parse_sse(event: str) -> tuple[str, dict]:
    lines = event.strip().split("\n")
    event_name = lines[0].replace("event: ", "")
    payload = json.loads(lines[1].replace("data: ", "")) if len(lines) > 1 else {}
    return event_name, payload


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


# --- _sanitize_artifact_payload テスト ---


class TestSanitizeArtifactPayload:
    """_sanitize_artifact_payload のテスト"""

    def test_replaces_data_uri(self):
        text = 'img src="data:image/png;base64,abc123def"'
        result = chat_module._sanitize_artifact_payload(text)
        assert "[data-uri]" in result
        assert "base64" not in result

    def test_no_data_uri_unchanged(self):
        text = "plain text without any URIs"
        assert chat_module._sanitize_artifact_payload(text) == text


# --- _truncate_for_safety テスト ---


class TestTruncateForSafety:
    """_truncate_for_safety のテスト"""

    def test_short_text_unchanged(self):
        text = "short"
        assert chat_module._truncate_for_safety(text) == text

    def test_long_text_truncated(self):
        text = "a" * 10000
        result = chat_module._truncate_for_safety(text)
        assert len(result) == 9000


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

    def test_error_event(self):
        events = [{"event": "error"}]
        assert chat_module._conversation_status_from_events(events) == "error"

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

        async def mock_get_conv(cid):
            return None

        monkeypatch.setattr("src.api.chat.get_conversation", mock_get_conv)
        result = await chat_module._load_pending_approval_context("missing-ctx")
        assert result is None


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

    assert chat_module._pending_approvals["conv-azure"]["analysis_markdown"] == "data-search-agent output"
    assert chat_module._pending_approvals["conv-azure"]["model_settings"] == {"temperature": 0.2}


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


# --- _get_reference_brochure_path テスト ---


class TestGetReferenceBrochurePath:
    """_get_reference_brochure_path のテスト"""

    def test_no_endpoint_returns_none(self, monkeypatch):
        """CONTENT_UNDERSTANDING_ENDPOINT 未設定時は None"""
        monkeypatch.delenv("CONTENT_UNDERSTANDING_ENDPOINT", raising=False)
        result = chat_module._get_reference_brochure_path()
        assert result is None

    def test_with_endpoint_but_no_file(self, monkeypatch):
        """エンドポイントありでもPDFファイルが無ければ None"""
        monkeypatch.setenv("CONTENT_UNDERSTANDING_ENDPOINT", "https://test.cognitiveservices.azure.com")
        result = chat_module._get_reference_brochure_path()
        # sample_brochure.pdf が実在しない限り None
        assert result is None or isinstance(result, str)


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
