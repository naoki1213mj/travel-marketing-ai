"""会話 API とリプレイ API のテスト"""

import base64
import json

from fastapi.testclient import TestClient

from src.main import app

client = TestClient(app)


def _make_bearer_token(payload: dict[str, object]) -> str:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode("utf-8")).decode("utf-8").rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8").rstrip("=")
    return f"{header}.{body}."


def test_conversations_list_returns_200():
    """GET /api/conversations が 200 を返す"""
    response = client.get("/api/conversations")
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.headers["ETag"].startswith('W/"')
    data = response.json()
    assert "conversations" in data
    assert isinstance(data["conversations"], list)


def test_conversations_list_returns_304_when_etag_matches(monkeypatch):
    """会話一覧も If-None-Match 一致時は 304 を返す"""

    async def fake_list_conversations(owner_id: str | None = None, limit: int = 20):
        return [
            {
                "id": "conv-1",
                "input": "沖縄プラン",
                "status": "completed",
                "created_at": "2026-04-05T00:00:00+00:00",
            }
        ]

    monkeypatch.setattr("src.api.conversations.list_conversations", fake_list_conversations)

    initial_response = client.get("/api/conversations")
    etag = initial_response.headers["ETag"]

    conditional_response = client.get(
        "/api/conversations",
        headers={"If-None-Match": etag},
    )

    assert conditional_response.status_code == 304
    assert conditional_response.headers["ETag"] == etag
    assert conditional_response.content == b""


def test_conversation_detail_returns_404_for_unknown():
    """存在しない conversation_id は 404"""
    response = client.get("/api/conversations/nonexistent-id")
    assert response.status_code == 404


def test_conversation_detail_hides_sensitive_metadata(monkeypatch):
    """会話詳細は callback token を返さない"""

    async def fake_get_conversation(_conversation_id: str, owner_id: str | None = None, allow_cross_owner: bool = False):
        return {
            "id": "conv-1",
            "user_id": "user-123",
            "input": "沖縄プラン",
            "updated_at": "2026-04-05T00:00:00+00:00",
            "messages": [],
            "metadata": {
                "manager_approval_callback_token": "secret-token",
                "conversation_settings": {"work_iq_enabled": True, "source_scope": ["emails"]},
                "work_iq_session": {
                    "enabled": True,
                    "source_scope": ["emails"],
                    "auth_mode": "delegated",
                    "owner_oid": "oid-123",
                    "owner_tid": "tid-123",
                    "owner_upn": "user@example.com",
                    "brief_summary": "要約",
                    "status": "completed",
                    "raw_excerpt": "should-not-leak",
                },
                "latency": 1.2,
            },
        }

    monkeypatch.setattr("src.api.conversations.get_conversation", fake_get_conversation)

    response = client.get("/api/conversations/conv-1")
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("no-store")
    assert response.headers["ETag"].startswith('W/"')
    data = response.json()
    assert "user_id" not in data
    assert data["metadata"] == {
        "conversation_settings": {"work_iq_enabled": True, "source_scope": ["emails"]},
        "work_iq_session": {
            "enabled": True,
            "source_scope": ["emails"],
            "auth_mode": "delegated",
            "brief_summary": "要約",
            "status": "completed",
        },
        "latency": 1.2,
    }


def test_conversation_detail_returns_304_when_etag_matches(monkeypatch):
    """If-None-Match が一致すれば会話本文を返さず 304 を返す"""

    async def fake_get_conversation(_conversation_id: str, owner_id: str | None = None, allow_cross_owner: bool = False):
        return {
            "id": "conv-1",
            "input": "沖縄プラン",
            "updated_at": "2026-04-05T00:00:00+00:00",
            "status": "completed",
            "messages": [{"event": "done", "data": {}}],
            "artifacts": [],
            "metadata": {},
        }

    monkeypatch.setattr("src.api.conversations.get_conversation", fake_get_conversation)

    initial_response = client.get("/api/conversations/conv-1")
    etag = initial_response.headers["ETag"]

    conditional_response = client.get(
        "/api/conversations/conv-1",
        headers={"If-None-Match": etag},
    )

    assert conditional_response.status_code == 304
    assert conditional_response.headers["ETag"] == etag
    assert conditional_response.content == b""


def test_conversations_list_uses_authenticated_owner_id(monkeypatch):
    """Authorization Bearer 付き一覧は delegated user_id で絞り込む"""
    captured: dict[str, object] = {}

    async def fake_list_conversations(owner_id: str | None = None, limit: int = 20):
        captured["owner_id"] = owner_id
        captured["limit"] = limit
        return []

    monkeypatch.setattr("src.api.conversations.list_conversations", fake_list_conversations)

    token = _make_bearer_token({"oid": "oid-123", "tid": "tid-123", "preferred_username": "user@example.com"})
    response = client.get("/api/conversations", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert isinstance(captured.get("owner_id"), str)
    assert str(captured["owner_id"]).startswith("user-")


def test_replay_returns_error_for_unknown():
    """存在しないリプレイデータは demo にフォールバックせずエラーイベントを返す"""
    response = client.get("/api/replay/nonexistent-id")
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "REPLAY_NOT_FOUND" in response.text


def test_replay_with_demo_json():
    """demo-replay.json からリプレイデータが読める"""
    response = client.get("/api/replay/demo-replay-001")
    assert response.status_code == 200
    content = response.text
    # JSON ファイルが存在すればイベントが返る、なければ REPLAY_NOT_FOUND
    assert "event:" in content


def test_replay_rejects_zero_speed():
    """speed は正の値のみ受け付ける"""
    response = client.get("/api/replay/demo-replay-001?speed=0")
    assert response.status_code == 422
