"""Voice Live API エンドポイントのテスト"""

from fastapi.testclient import TestClient

import src.main as main_module
from src.main import app

client = TestClient(app)


def test_voice_config_returns_defaults_when_env_missing(monkeypatch):
    """Voice config は未設定時でも 200 を返し、接続先は空文字列になる"""
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.delenv("VOICE_AGENT_NAME", raising=False)
    monkeypatch.delenv("VOICE_SPA_CLIENT_ID", raising=False)
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    response = client.get("/api/voice-config")

    assert response.status_code == 200
    assert response.json() == {
        "agent_name": "travel-voice-orchestrator",
        "client_id": "",
        "tenant_id": "",
        "resource_name": "",
        "project_name": "",
        "voice": "ja-JP-NanamiNeural",
        "vad_type": "azure_semantic_vad",
        "endpoint": "",
        "api_version": "2026-01-01-preview",
    }


def test_voice_config_parses_project_endpoint(monkeypatch):
    """Voice config は Foundry project endpoint から接続情報を組み立てる"""
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.setenv("VOICE_AGENT_NAME", "travel-voice")
    monkeypatch.setenv("VOICE_SPA_CLIENT_ID", "client-id")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://travelfoundry.services.ai.azure.com/api/projects/teamd",
    )

    response = client.get("/api/voice-config")

    assert response.status_code == 200
    assert response.json()["resource_name"] == "travelfoundry"
    assert response.json()["project_name"] == "teamd"
    assert response.json()["endpoint"] == "wss://travelfoundry.services.ai.azure.com/voice-live/realtime"


def test_voice_token_returns_gone_guidance(monkeypatch):
    """Voice token endpoint は browser delegated auth へ誘導する"""
    monkeypatch.setattr(main_module, "_API_KEY", "")

    response = client.get("/api/voice-token")

    assert response.status_code == 410
    assert response.json() == {
        "error": "Voice token endpoint disabled",
        "code": "VOICE_TOKEN_ENDPOINT_DISABLED",
        "message": (
            "Use /api/voice-config and browser delegated MSAL auth with "
            "https://cognitiveservices.azure.com/user_impersonation."
        ),
    }
