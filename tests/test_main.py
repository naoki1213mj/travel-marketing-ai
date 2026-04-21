"""main モジュールのミドルウェアテスト"""

from pathlib import Path

from fastapi.testclient import TestClient

import src.main as main_module
from src.main import app

client = TestClient(app)


def test_health_includes_request_id_header(monkeypatch):
    """すべてのレスポンスに X-Request-Id を付与する"""
    monkeypatch.setattr(main_module, "_API_KEY", "")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["X-Request-Id"]


def test_api_key_middleware_blocks_protected_api(monkeypatch):
    """API_KEY 設定時は保護対象 API を x-api-key なしで拒否する"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/voice-config")

    assert response.status_code == 401
    assert response.json() == {"error": "Unauthorized — invalid or missing API key"}


def test_api_key_middleware_allows_authorized_request(monkeypatch):
    """正しい x-api-key があれば保護対象 API にアクセスできる"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/voice-config", headers={"x-api-key": "test-secret"})

    assert response.status_code == 200
    assert response.json()["agent_name"] == "travel-voice-orchestrator"


def test_api_key_middleware_keeps_health_exempt(monkeypatch):
    """health/readiness は API_KEY 設定時でも認証不要のままにする"""
    monkeypatch.setattr(main_module, "_API_KEY", "test-secret")

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_auth_redirect_bridge_serves_no_store_html(monkeypatch, tmp_path: Path):
    """auth redirect bridge は no-store で静的 HTML を返す"""
    redirect_file = tmp_path / "auth-redirect.html"
    redirect_file.write_text("<html>redirect</html>", encoding="utf-8")
    monkeypatch.setattr(main_module, "_STATIC_DIR", str(tmp_path))

    response = client.get("/auth-redirect.html")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "redirect" in response.text
