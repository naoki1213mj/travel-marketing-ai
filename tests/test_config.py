"""config モジュールのユニットテスト"""

from src.config import AppSettings, get_missing_required_settings, get_settings, is_production_environment


def test_get_settings_returns_all_fields(monkeypatch):
    """get_settings が AppSettings の全キーを返す"""
    # 環境変数をクリアして確実にデフォルト値を使う
    for key in [
        "AZURE_AI_PROJECT_ENDPOINT",
        "MODEL_NAME",
        "CONTENT_SAFETY_ENDPOINT",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "ENVIRONMENT",
        "COSMOS_DB_ENDPOINT",
        "FABRIC_SQL_ENDPOINT",
        "ALLOWED_ORIGINS",
        "CONTENT_UNDERSTANDING_ENDPOINT",
        "SPEECH_SERVICE_ENDPOINT",
        "SPEECH_SERVICE_REGION",
        "LOGIC_APP_CALLBACK_URL",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()
    expected_keys = set(AppSettings.__annotations__.keys())
    assert set(settings.keys()) == expected_keys


def test_is_production_environment_true(monkeypatch):
    """ENVIRONMENT=production で True を返す"""
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert is_production_environment() is True


def test_is_production_environment_false(monkeypatch):
    """ENVIRONMENT=development で False を返す"""
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert is_production_environment() is False


def test_get_missing_required_settings(monkeypatch):
    """本番環境で project_endpoint 未設定時に不足リストに含まれる"""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)

    missing = get_missing_required_settings()
    assert "AZURE_AI_PROJECT_ENDPOINT" in missing
    assert "CONTENT_SAFETY_ENDPOINT" in missing


def test_default_values(monkeypatch):
    """model_name のデフォルト値が gpt-5-4-mini"""
    monkeypatch.delenv("MODEL_NAME", raising=False)
    settings = get_settings()
    assert settings["model_name"] == "gpt-5-4-mini"
