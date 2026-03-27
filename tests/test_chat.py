"""チャットエンドポイントのバリデーションテスト"""

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def test_chat_returns_sse_stream():
    """POST /api/chat が SSE ストリームを返す"""
    response = client.post(
        "/api/chat",
        json={"message": "テスト入力"},
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")


def test_chat_requires_message():
    """POST /api/chat に message がなければ 422 を返す"""
    response = client.post("/api/chat", json={})
    assert response.status_code == 422


def test_chat_sse_events_contain_expected_types():
    """SSE ストリームに期待するイベント種別が含まれる"""
    response = client.post(
        "/api/chat",
        json={"message": "春の沖縄ファミリー向けプランを企画して"},
    )
    content = response.text
    assert "event: agent_progress" in content
    assert "event: text" in content
    assert "event: done" in content
