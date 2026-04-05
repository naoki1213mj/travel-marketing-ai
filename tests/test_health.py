"""ヘルスチェックエンドポイントのテスト"""

from fastapi.testclient import TestClient

from src import config as config_module
from src.main import app

client = TestClient(app)


def _disable_azd_env(monkeypatch) -> None:
    """テスト中は実マシンの azd env を参照しない。"""
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})


def test_health_returns_200():
    """GET /api/health が 200 を返す"""
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_ready_in_development(monkeypatch):
    """開発環境では未設定があっても readiness は ready を返す"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    response = client.get("/api/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "missing": []}


def test_ready_returns_503_when_required_settings_missing_in_production(monkeypatch):
    """本番環境では必須設定不足時に readiness が 503 を返す"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    response = client.get("/api/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "missing": ["AZURE_AI_PROJECT_ENDPOINT"],
    }
