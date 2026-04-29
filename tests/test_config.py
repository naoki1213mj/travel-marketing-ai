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
        "FABRIC_LAKEHOUSE_DATABASE",
        "FABRIC_DATABASE_NAME",
        "FABRIC_SALES_TABLE",
        "FABRIC_REVIEWS_TABLE",
        "FABRIC_DATA_AGENT_RUNTIME",
        "ENABLE_FABRIC_DATA_AGENT_REST",
        "ALLOWED_ORIGINS",
        "CONTENT_UNDERSTANDING_ENDPOINT",
        "SPEECH_SERVICE_ENDPOINT",
        "SPEECH_SERVICE_REGION",
        "LOGIC_APP_CALLBACK_URL",
        "MANAGER_APPROVAL_TRIGGER_URL",
        "GPT_IMAGE_15_DEPLOYMENT_NAME",
        "GPT_IMAGE_2_DEPLOYMENT_NAME",
        "ENABLE_MODEL_ROUTER",
        "MODEL_ROUTER_ENDPOINT",
        "MODEL_ROUTER_DEPLOYMENT_NAME",
        "MODEL_DEPLOYMENT_ALLOWLIST",
        "ENABLE_GPT_55",
        "GPT_55_DEPLOYMENT_NAME",
        "ENABLE_FOUNDRY_TRACING",
        "ENABLE_CONTINUOUS_MONITORING",
        "CONTINUOUS_MONITORING_SAMPLE_RATE",
        "ENABLE_EVALUATION_LOGGING",
        "EVALUATION_LOG_RETENTION_DAYS",
        "ENABLE_COST_METRICS",
        "MCP_REGISTRY_ENDPOINT",
        "ENABLE_SOURCE_INGESTION",
        "SOURCE_INGESTION_ENDPOINT",
        "SOURCE_MAX_ITEMS_PER_OWNER",
        "SOURCE_TTL_SECONDS",
        "SOURCE_MAX_TEXT_CHARS",
        "SOURCE_MAX_PDF_BYTES",
        "SOURCE_MAX_AUDIO_SECONDS",
        "SOURCE_MAX_AUDIO_BYTES",
        "ENABLE_VOICE_TALK_TO_START",
        "ENABLE_MAI_TRANSCRIBE_1",
        "MAI_TRANSCRIBE_1_ENDPOINT",
        "MAI_TRANSCRIBE_1_DEPLOYMENT_NAME",
        "MAI_TRANSCRIBE_1_API_PATH",
        "TRUST_AUTH_HEADER_CLAIMS",
        "TRUSTED_AUTH_HEADER_NAME",
        "TRUSTED_AUTH_HEADER_VALUE",
        "REQUIRE_AUTHENTICATED_OWNER",
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


def test_fabric_lakehouse_database_default_and_alias(monkeypatch):
    """Fabric Lakehouse database 名は既定値と alias で切り替えられる。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("FABRIC_LAKEHOUSE_DATABASE", raising=False)
    monkeypatch.setenv("FABRIC_DATABASE_NAME", "Travel_Lakehouse_v2")

    settings = get_settings()

    assert settings["fabric_lakehouse_database"] == "Travel_Lakehouse_v2"


def test_fabric_table_defaults(monkeypatch):
    """Fabric table 名は旧 Lakehouse の既定値を維持する。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("FABRIC_SALES_TABLE", raising=False)
    monkeypatch.delenv("FABRIC_REVIEWS_TABLE", raising=False)

    settings = get_settings()

    assert settings["fabric_sales_table"] == "sales_results"
    assert settings["fabric_reviews_table"] == "customer_reviews"


def test_fabric_data_agent_runtime_defaults_to_sql(monkeypatch):
    """Fabric Data Agent REST preview は既定で使わず SQL primary にする。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("FABRIC_DATA_AGENT_RUNTIME", raising=False)
    monkeypatch.delenv("ENABLE_FABRIC_DATA_AGENT_REST", raising=False)

    settings = get_settings()

    assert settings["fabric_data_agent_runtime"] == "sql"


def test_identity_boundary_defaults_to_untrusted_header_claims(monkeypatch):
    """未設定時は Authorization claims を信頼しない。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("TRUST_AUTH_HEADER_CLAIMS", raising=False)
    monkeypatch.delenv("REQUIRE_AUTHENTICATED_OWNER", raising=False)

    settings = get_settings()

    assert settings["trust_auth_header_claims"] == "false"
    assert settings["require_authenticated_owner"] == "false"


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


def test_roadmap_capability_defaults(monkeypatch):
    """ロードマップ機能は明示設定がない限り既定で無効。"""
    _disable_azd_env(monkeypatch)
    for key in [
        "ENABLE_MODEL_ROUTER",
        "ENABLE_GPT_55",
        "ENABLE_FOUNDRY_TRACING",
        "ENABLE_CONTINUOUS_MONITORING",
        "CONTINUOUS_MONITORING_SAMPLE_RATE",
        "ENABLE_COST_METRICS",
        "ENABLE_SOURCE_INGESTION",
        "ENABLE_VOICE_TALK_TO_START",
        "ENABLE_MAI_TRANSCRIBE_1",
        "MODEL_ROUTER_ENDPOINT",
        "MODEL_ROUTER_DEPLOYMENT_NAME",
        "MODEL_DEPLOYMENT_ALLOWLIST",
        "GPT_55_DEPLOYMENT_NAME",
        "MCP_REGISTRY_ENDPOINT",
        "SOURCE_INGESTION_ENDPOINT",
        "SOURCE_MAX_ITEMS_PER_OWNER",
        "SOURCE_TTL_SECONDS",
        "SOURCE_MAX_TEXT_CHARS",
        "SOURCE_MAX_PDF_BYTES",
        "SOURCE_MAX_AUDIO_SECONDS",
        "SOURCE_MAX_AUDIO_BYTES",
        "MAI_TRANSCRIBE_1_ENDPOINT",
        "MAI_TRANSCRIBE_1_DEPLOYMENT_NAME",
        "MAI_TRANSCRIBE_1_API_PATH",
    ]:
        monkeypatch.delenv(key, raising=False)

    settings = config_module.get_settings()

    assert settings["enable_model_router"] == "false"
    assert settings["enable_gpt_55"] == "false"
    assert settings["enable_foundry_tracing"] == "false"
    assert settings["enable_continuous_monitoring"] == "false"
    assert settings["continuous_monitoring_sample_rate"] == "0.1"
    assert settings["enable_evaluation_logging"] == "false"
    assert settings["evaluation_log_retention_days"] == "30"
    assert settings["enable_cost_metrics"] == "false"
    assert settings["enable_source_ingestion"] == "false"
    assert settings["enable_voice_talk_to_start"] == "false"
    assert settings["enable_mai_transcribe_1"] == "false"
    assert settings["model_router_endpoint"] == ""
    assert settings["model_router_deployment_name"] == ""
    assert settings["model_deployment_allowlist"] == ""
    assert settings["gpt_55_deployment_name"] == ""
    assert settings["mcp_registry_endpoint"] == ""
    assert settings["source_ingestion_endpoint"] == ""
    assert settings["source_max_items_per_owner"] == "20"
    assert settings["source_ttl_seconds"] == "604800"
    assert settings["source_max_text_chars"] == "20000"
    assert settings["source_max_pdf_bytes"] == "10485760"
    assert settings["source_max_audio_seconds"] == "1800"
    assert settings["source_max_audio_bytes"] == "26214400"
    assert settings["mai_transcribe_1_endpoint"] == ""
    assert settings["mai_transcribe_1_deployment_name"] == ""
    assert settings["mai_transcribe_1_api_path"] == ""


def test_roadmap_capability_env_aliases(monkeypatch):
    """ロードマップ機能の env alias を解決できる。"""
    _disable_azd_env(monkeypatch)
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("GPT_55_DEPLOYMENT_NAME", raising=False)
    monkeypatch.setenv("MODEL_ROUTER_ENABLED", "true")
    monkeypatch.setenv("MODEL_ROUTER_MODEL_NAME", "model-router")
    monkeypatch.setenv("ALLOWED_MODEL_DEPLOYMENTS", "custom-a,custom-b")
    monkeypatch.setenv("GPT_5_5_DEPLOYMENT_NAME", "gpt-5.5")
    monkeypatch.setenv("MCP_REGISTRY_URL", "https://registry.example/mcp")
    monkeypatch.setenv("SOURCE_INGESTION_URL", "https://source.example/ingest")

    settings = config_module.get_settings()

    assert settings["enable_model_router"] == "true"
    assert settings["model_router_deployment_name"] == "model-router"
    assert settings["model_deployment_allowlist"] == "custom-a,custom-b"
    assert settings["gpt_55_deployment_name"] == "gpt-5.5"
    assert settings["mcp_registry_endpoint"] == "https://registry.example/mcp"
    assert settings["source_ingestion_endpoint"] == "https://source.example/ingest"


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
