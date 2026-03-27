"""SSE フォーマットのテスト"""

import json

from src.api.chat import SSEEventType, format_sse


def test_format_sse_produces_valid_format():
    """format_sse が SSE 仕様のフォーマットを返す"""
    result = format_sse(SSEEventType.TEXT, {"content": "テスト"})
    assert result.startswith("event: text\n")
    assert "data: " in result
    assert result.endswith("\n\n")


def test_format_sse_data_is_valid_json():
    """format_sse の data フィールドが有効な JSON である"""
    result = format_sse(SSEEventType.AGENT_PROGRESS, {"agent": "test", "step": 1})
    data_line = [line for line in result.split("\n") if line.startswith("data: ")][0]
    data_json = json.loads(data_line[len("data: "):])
    assert data_json["agent"] == "test"
    assert data_json["step"] == 1


def test_format_sse_handles_japanese():
    """format_sse が日本語を正しくエンコードする（ensure_ascii=False）"""
    result = format_sse(SSEEventType.TEXT, {"content": "日本語テスト"})
    assert "日本語テスト" in result


def test_sse_event_types():
    """SSEEventType が 8 種類定義されている"""
    assert len(SSEEventType) == 8
    assert SSEEventType.AGENT_PROGRESS == "agent_progress"
    assert SSEEventType.DONE == "done"
