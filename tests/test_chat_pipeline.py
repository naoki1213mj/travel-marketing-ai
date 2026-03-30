"""チャット逐次オーケストレーションのテスト"""

import json

import pytest

from src.api import chat as chat_module


def _parse_sse(event: str) -> tuple[str, dict]:
    lines = event.strip().split("\n")
    event_name = lines[0].replace("event: ", "")
    payload = json.loads(lines[1].replace("data: ", "")) if len(lines) > 1 else {}
    return event_name, payload


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
