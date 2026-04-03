"""Voice Live API エンドポイントのテスト"""

from fastapi.testclient import TestClient

import src.api.voice as voice_module
import src.main as main_module
from src.main import app

client = TestClient(app)


class _Token:
    def __init__(self, token: str = "test-token", expires_on: int = 1234567890):
        self.token = token
        self.expires_on = expires_on


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


def test_voice_token_returns_connection_info(monkeypatch):
    """Voice token は AAD トークンと Foundry 接続先を返す"""
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://travelfoundry.services.ai.azure.com/api/projects/teamd",
    )

    class _Credential:
        def get_token(self, scope: str) -> _Token:
            assert scope == "https://ai.azure.com/.default"
            return _Token(token="voice-token", expires_on=999)

    monkeypatch.setattr(voice_module, "DefaultAzureCredential", lambda: _Credential())

    response = client.get("/api/voice-token")

    assert response.status_code == 200
    assert response.json() == {
        "token": "voice-token",
        "expires_on": 999,
        "resource_name": "travelfoundry",
        "project_name": "teamd",
        "endpoint": "wss://travelfoundry.services.ai.azure.com/voice-live/realtime",
        "api_version": "2026-01-01-preview",
    }


def test_voice_token_returns_empty_endpoint_without_project_endpoint(monkeypatch):
    """project endpoint 未設定時でも token API は不正な URL を返さない"""
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    class _Credential:
        def get_token(self, scope: str) -> _Token:
            assert scope == "https://ai.azure.com/.default"
            return _Token(token="voice-token", expires_on=999)

    monkeypatch.setattr(voice_module, "DefaultAzureCredential", lambda: _Credential())

    response = client.get("/api/voice-token")

    assert response.status_code == 200
    assert response.json()["resource_name"] == ""
    assert response.json()["project_name"] == ""
    assert response.json()["endpoint"] == ""


def test_voice_token_returns_503_when_credential_fails(monkeypatch):
    """AAD トークン取得失敗時は 503 を返す"""
    monkeypatch.setattr(main_module, "_API_KEY", "")

    class _Credential:
        def get_token(self, _scope: str) -> _Token:
            raise RuntimeError("credential failure")

    monkeypatch.setattr(voice_module, "DefaultAzureCredential", lambda: _Credential())

    response = client.get("/api/voice-token")

    assert response.status_code == 503
    assert response.json() == {"error": "Voice token unavailable"}
