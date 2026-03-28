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
    """SSE ストリームに期待するイベント種別が含まれる（承認要求で一旦停止する）"""
    response = client.post(
        "/api/chat",
        json={"message": "春の沖縄ファミリー向けプランを企画して"},
    )
    content = response.text
    assert "event: agent_progress" in content
    assert "event: text" in content
    assert "event: approval_request" in content


def test_approve_with_approval_returns_post_approval_events():
    """承認すると Agent3→Agent4 の SSE イベントが返る"""
    response = client.post(
        "/api/chat/test-thread/approve",
        json={"conversation_id": "test-thread", "response": "承認"},
    )
    assert response.status_code == 200
    content = response.text
    assert "event: agent_progress" in content
    assert "regulation-check-agent" in content
    assert "brochure-gen-agent" in content
    assert "event: done" in content


def test_approve_with_revision_returns_revised_plan():
    """修正指示すると Agent2 再実行の SSE イベントが返る"""
    response = client.post(
        "/api/chat/test-thread/approve",
        json={"conversation_id": "test-thread", "response": "キャッチコピーを変えて"},
    )
    assert response.status_code == 200
    content = response.text
    assert "marketing-plan-agent" in content
    assert "event: approval_request" in content


def test_chat_refine_with_conversation_id():
    """conversation_id 付きのリクエストはマルチターン修正として処理される"""
    response = client.post(
        "/api/chat",
        json={"message": "キャッチコピーをもっとポップに", "conversation_id": "existing-conv"},
    )
    assert response.status_code == 200
    content = response.text
    assert "event: agent_progress" in content
    # 修正対話はモック経路で approval_request を返す（再承認フロー）
    assert "event: text" in content or "event: approval_request" in content
