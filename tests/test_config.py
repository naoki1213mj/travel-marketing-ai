"""config モジュールのユニットテスト"""

from src import config as config_module
from src.config import AppSettings, get_missing_required_settings, get_settings, is_production_environment


def _disable_azd_env(monkeypatch) -> None:
    """テスト中は実マシンの azd env を参照しない。"""
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})


def test_get_settings_returns_all_fields(monkeypatch):
    """get_settings が AppSettings の全キーを返す"""
    _disable_azd_env(monkeypatch)
    # 環境変数をクリアして確実にデフォルト値を使う
    for key in [
        "AZURE_AI_PROJECT_ENDPOINT",
        "MODEL_NAME",
        "ENTRA_TENANT_ID",
        "AZURE_TENANT_ID",
        "ENTRA_CLIENT_ID",
        "VOICE_SPA_CLIENT_ID",
        "WORK_IQ_TIMEOUT_SECONDS",
        "IMPROVEMENT_MCP_ENDPOINT",
        "IMPROVEMENT_MCP_API_KEY",
        "IMPROVEMENT_MCP_API_KEY_HEADER",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "ENVIRONMENT",
        "COSMOS_DB_ENDPOINT",
        "FABRIC_SQL_ENDPOINT",
        "ALLOWED_ORIGINS",
        "CONTENT_UNDERSTANDING_ENDPOINT",
        "SPEECH_SERVICE_ENDPOINT",
        "SPEECH_SERVICE_REGION",
        "LOGIC_APP_CALLBACK_URL",
        "MANAGER_APPROVAL_TRIGGER_URL",
        "GPT_IMAGE_15_DEPLOYMENT_NAME",
        "GPT_IMAGE_2_DEPLOYMENT_NAME",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = get_settings()
    expected_keys = set(AppSettings.__annotations__.keys())
    assert set(settings.keys()) == expected_keys


def test_is_production_environment_true(monkeypatch):
    """ENVIRONMENT=production で True を返す"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert is_production_environment() is True


def test_is_production_environment_false(monkeypatch):
    """ENVIRONMENT=development で False を返す"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert is_production_environment() is False


def test_get_missing_required_settings(monkeypatch):
    """本番環境で project_endpoint 未設定時に不足リストに含まれる"""
    _disable_azd_env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    missing = get_missing_required_settings()
    assert "AZURE_AI_PROJECT_ENDPOINT" in missing
    assert len(missing) == 1


def test_default_values(monkeypatch):
    """model_name のデフォルト値が gpt-5-4-mini"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    settings = get_settings()
    assert settings["model_name"] == "gpt-5-4-mini"


def test_improvement_mcp_header_default(monkeypatch):
    """MCP API キーヘッダーは APIM 既定名を使う"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("IMPROVEMENT_MCP_API_KEY_HEADER", raising=False)

    settings = get_settings()

    assert settings["improvement_mcp_api_key_header"] == "Ocp-Apim-Subscription-Key"


def test_work_iq_timeout_default(monkeypatch):
    """Work IQ timeout の環境既定値は 120 秒を維持する"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("WORK_IQ_TIMEOUT_SECONDS", raising=False)

    settings = get_settings()

    assert settings["work_iq_timeout_seconds"] == "120"


def test_image_deployment_name_defaults(monkeypatch):
    """画像 deployment 名はモデル名を既定値として解決する"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("GPT_IMAGE_15_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("GPT_IMAGE_2_DEPLOYMENT_NAME", raising=False)

    settings = get_settings()

    assert settings["gpt_image_15_deployment_name"] == "gpt-image-1.5"
    assert settings["gpt_image_2_deployment_name"] == "gpt-image-2"


def test_foundry_env_aliases(monkeypatch):
    """FOUNDRY_* エイリアス環境変数も解決できる"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("MODEL_NAME", raising=False)
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("FOUNDRY_MODEL", "gpt-5-4-mini")

    settings = get_settings()

    assert settings["project_endpoint"] == "https://example.services.ai.azure.com/api/projects/demo"
    assert settings["model_name"] == "gpt-5-4-mini"


def test_entra_env_aliases(monkeypatch):
    """ENTRA / Azure alias 環境変数も解決できる"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("ENTRA_TENANT_ID", raising=False)
    monkeypatch.delenv("ENTRA_CLIENT_ID", raising=False)
    monkeypatch.setenv("AZURE_TENANT_ID", "tenant-123")
    monkeypatch.setenv("VOICE_SPA_CLIENT_ID", "client-123")

    settings = get_settings()

    assert settings["entra_tenant_id"] == "tenant-123"
    assert settings["entra_client_id"] == "client-123"


def test_get_settings_falls_back_to_azd_env(monkeypatch):
    """process env 未設定時は azd env の値を補完する"""
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("IMPROVEMENT_MCP_ENDPOINT", raising=False)
    monkeypatch.setattr(
        config_module,
        "_get_azd_env_values",
        lambda: {
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo",
            "IMPROVEMENT_MCP_ENDPOINT": "https://example.azure-api.net/improvement-mcp/runtime/webhooks/mcp",
        },
    )

    settings = config_module.get_settings()

    assert settings["project_endpoint"] == "https://example.services.ai.azure.com/api/projects/demo"
    assert settings["improvement_mcp_endpoint"] == "https://example.azure-api.net/improvement-mcp/runtime/webhooks/mcp"


def test_process_env_overrides_azd_env(monkeypatch):
    """process env が azd env より優先される"""
    monkeypatch.setenv("IMPROVEMENT_MCP_ENDPOINT", "https://process.example/mcp")
    monkeypatch.setattr(
        config_module,
        "_get_azd_env_values",
        lambda: {"IMPROVEMENT_MCP_ENDPOINT": "https://azd.example/mcp"},
    )

    settings = config_module.get_settings()

    assert settings["improvement_mcp_endpoint"] == "https://process.example/mcp"


def test_get_missing_required_settings_accepts_azd_env(monkeypatch):
    """本番環境でも azd env に project endpoint があれば不足扱いにしない"""
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
    monkeypatch.setattr(
        config_module,
        "_get_azd_env_values",
        lambda: {
            "ENVIRONMENT": "production",
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.services.ai.azure.com/api/projects/demo",
        },
    )

    missing = config_module.get_missing_required_settings()

    assert missing == []
