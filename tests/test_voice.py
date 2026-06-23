"""音声入力（Azure Speech STT）エンドポイントのテスト。"""

import httpx
import pytest
from fastapi.testclient import TestClient

import src.agent_client as agent_client_module
import src.api.voice as voice_module
import src.main as main_module
from src import config as config_module
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _disable_rate_limiter():
    """speech-token のレート制限はテストでは無効化する。"""
    limiter = app.state.limiter
    was_enabled = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = was_enabled


class _FakeToken:
    def __init__(self, token: str) -> None:
        self.token = token


class _FakeCredential:
    def __init__(self, token: str = "aad-access-token") -> None:
        self._token = token
        self.scopes: list[str] = []

    def get_token(self, scope: str):
        self.scopes.append(scope)
        return _FakeToken(self._token)


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttpClient:
    def __init__(self, response: _FakeResponse | None = None, exc: Exception | None = None) -> None:
        self._response = response
        self._exc = exc
        self.calls: list[tuple[str, dict]] = []

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc is not None:
            raise self._exc
        return self._response


def _configure_speech_env(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
    monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://example.cognitiveservices.azure.com/")
    monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")


def _clear_speech_env(monkeypatch) -> None:
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
    monkeypatch.delenv("SPEECH_SERVICE_ENDPOINT", raising=False)
    monkeypatch.delenv("SPEECH_SERVICE_REGION", raising=False)


def test_voice_config_returns_msal_client_settings(monkeypatch):
    """/api/voice-config は Work IQ delegated 認証用の MSAL 設定を返す（後方互換）。"""
    monkeypatch.setattr(main_module, "_API_KEY", "")
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.setenv("VOICE_SPA_CLIENT_ID", "spa-client-id")
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-id")

    response = client.get("/api/voice-config")

    assert response.status_code == 200
    assert response.json() == {"client_id": "spa-client-id", "tenant_id": "tenant-id"}


def test_speech_config_reports_web_speech_when_unconfigured(monkeypatch):
    """Speech 未設定時は web_speech モード（ブラウザフォールバック）を返す。"""
    _clear_speech_env(monkeypatch)

    response = client.get("/api/speech-config")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "web_speech",
        "region": "",
        "language": "ja-JP",
        "available": False,
    }


def test_speech_config_reports_azure_speech_when_configured(monkeypatch):
    """Speech 設定時は azure_speech モードと region を返す（機密値は含まない）。"""
    _configure_speech_env(monkeypatch)

    response = client.get("/api/speech-config")

    assert response.status_code == 200
    assert response.json() == {
        "mode": "azure_speech",
        "region": "eastus2",
        "language": "ja-JP",
        "available": True,
    }


def test_voice_token_returns_gone_guidance(monkeypatch):
    """旧 voice token endpoint は /api/speech-token へ誘導する。"""
    monkeypatch.setattr(main_module, "_API_KEY", "")

    response = client.get("/api/voice-token")

    assert response.status_code == 410
    body = response.json()
    assert body["code"] == "VOICE_TOKEN_ENDPOINT_DISABLED"
    assert "/api/speech-token" in body["message"]


def test_speech_token_unconfigured_returns_503(monkeypatch):
    """Speech 未設定時は 503 SPEECH_STT_NOT_CONFIGURED を返す。"""
    _clear_speech_env(monkeypatch)

    response = client.get("/api/speech-token")

    assert response.status_code == 503
    assert response.json()["code"] == "SPEECH_STT_NOT_CONFIGURED"


def test_speech_token_issues_short_lived_token(monkeypatch):
    """Managed Identity で issueToken を呼び、authorization token と region を返す。"""
    _configure_speech_env(monkeypatch)
    credential = _FakeCredential("aad-access-token")
    monkeypatch.setattr(agent_client_module, "get_shared_credential", lambda: credential)
    fake_client = _FakeHttpClient(response=_FakeResponse(200, "service-token-value"))
    monkeypatch.setattr(voice_module, "get_http_client", lambda: fake_client)

    response = client.get("/api/speech-token")

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"] == "service-token-value"
    assert payload["region"] == "eastus2"
    assert payload["language"] == "ja-JP"
    assert isinstance(payload["expires_at"], int)
    assert response.headers["cache-control"] == "no-store"

    # custom-domain の issueToken を Bearer トークンで呼び出す。
    assert len(fake_client.calls) == 1
    url, kwargs = fake_client.calls[0]
    assert url == "https://example.cognitiveservices.azure.com/sts/v1.0/issueToken"
    assert kwargs["headers"]["Authorization"] == "Bearer aad-access-token"
    assert credential.scopes == ["https://cognitiveservices.azure.com/.default"]


def test_speech_token_propagates_issue_token_failure(monkeypatch):
    """issueToken が 4xx を返した場合は 503 にフォールバックする。"""
    _configure_speech_env(monkeypatch)
    monkeypatch.setattr(agent_client_module, "get_shared_credential", lambda: _FakeCredential())
    fake_client = _FakeHttpClient(response=_FakeResponse(403, "Forbidden"))
    monkeypatch.setattr(voice_module, "get_http_client", lambda: fake_client)

    response = client.get("/api/speech-token")

    assert response.status_code == 503
    assert response.json()["code"] == "SPEECH_TOKEN_UNAVAILABLE"


def test_speech_token_handles_http_error(monkeypatch):
    """issueToken の通信エラー時も 503 にフォールバックする。"""
    _configure_speech_env(monkeypatch)
    monkeypatch.setattr(agent_client_module, "get_shared_credential", lambda: _FakeCredential())
    fake_client = _FakeHttpClient(exc=httpx.ConnectError("boom"))
    monkeypatch.setattr(voice_module, "get_http_client", lambda: fake_client)

    response = client.get("/api/speech-token")

    assert response.status_code == 503
    assert response.json()["code"] == "SPEECH_TOKEN_UNAVAILABLE"


def test_speech_token_handles_empty_token(monkeypatch):
    """issueToken が空文字を返した場合は 503 にフォールバックする。"""
    _configure_speech_env(monkeypatch)
    monkeypatch.setattr(agent_client_module, "get_shared_credential", lambda: _FakeCredential())
    fake_client = _FakeHttpClient(response=_FakeResponse(200, "   "))
    monkeypatch.setattr(voice_module, "get_http_client", lambda: fake_client)

    response = client.get("/api/speech-token")

    assert response.status_code == 503
    assert response.json()["code"] == "SPEECH_TOKEN_UNAVAILABLE"


def test_cognitive_services_scope_constant():
    """Speech STT は cognitiveservices.azure.com スコープを使う（ai.azure.com ではない）。"""
    assert voice_module._COGNITIVE_SERVICES_SCOPE == "https://cognitiveservices.azure.com/.default"
