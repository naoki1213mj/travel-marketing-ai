"""アプリケーション設定。TypedDict + load_settings パターンで環境変数をロードする。"""

import os
import shutil
import subprocess
from functools import lru_cache
from typing import TypedDict

from dotenv import load_dotenv

# ローカル開発用に .env を読み込む
load_dotenv(override=False)


class AppSettings(TypedDict):
    """アプリケーションの環境変数設定"""

    project_endpoint: str
    model_name: str
    entra_tenant_id: str
    entra_client_id: str
    work_iq_timeout_seconds: str
    improvement_mcp_endpoint: str
    improvement_mcp_api_key: str
    improvement_mcp_api_key_header: str
    applicationinsights_connection_string: str
    environment: str
    cosmos_db_endpoint: str
    fabric_sql_endpoint: str
    fabric_lakehouse_database: str
    fabric_sales_table: str
    fabric_reviews_table: str
    fabric_data_agent_runtime: str
    allowed_origins: str
    content_understanding_endpoint: str
    speech_service_endpoint: str
    speech_service_region: str
    logic_app_callback_url: str
    manager_approval_trigger_url: str
    public_app_base_url: str
    fabric_data_agent_url: str
    image_project_endpoint_mai: str
    gpt_image_15_deployment_name: str
    gpt_image_2_deployment_name: str
    marketing_plan_runtime: str
    marketing_plan_prompt_agent_name: str
    work_iq_runtime: str
    enable_github_copilot_review_agent: str
    enable_model_router: str
    model_router_endpoint: str
    model_router_deployment_name: str
    model_deployment_allowlist: str
    enable_gpt_55: str
    gpt_55_deployment_name: str
    enable_foundry_tracing: str
    enable_continuous_monitoring: str
    continuous_monitoring_sample_rate: str
    enable_evaluation_logging: str
    evaluation_log_retention_days: str
    enable_cost_metrics: str
    mcp_registry_endpoint: str
    enable_source_ingestion: str
    source_ingestion_endpoint: str
    source_max_items_per_owner: str
    source_ttl_seconds: str
    source_max_text_chars: str
    source_max_pdf_bytes: str
    source_max_audio_seconds: str
    source_max_audio_bytes: str
    enable_voice_talk_to_start: str
    enable_mai_transcribe_1: str
    mai_transcribe_1_endpoint: str
    mai_transcribe_1_deployment_name: str
    mai_transcribe_1_api_path: str
    trust_auth_header_claims: str
    trusted_auth_header_name: str
    trusted_auth_header_value: str
    require_authenticated_owner: str


# 環境変数の優先順位。GA で一般化した FOUNDRY_* も受け付ける。
_ENV_CANDIDATES: dict[str, tuple[str, ...]] = {
    "project_endpoint": ("AZURE_AI_PROJECT_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT"),
    "model_name": ("MODEL_NAME", "FOUNDRY_MODEL"),
    "entra_tenant_id": ("ENTRA_TENANT_ID", "AZURE_TENANT_ID"),
    "entra_client_id": ("ENTRA_CLIENT_ID", "VOICE_SPA_CLIENT_ID"),
    "work_iq_timeout_seconds": ("WORK_IQ_TIMEOUT_SECONDS",),
    "improvement_mcp_endpoint": ("IMPROVEMENT_MCP_ENDPOINT", "IMPROVEMENT_MCP_URL"),
    "improvement_mcp_api_key": ("IMPROVEMENT_MCP_API_KEY",),
    "improvement_mcp_api_key_header": ("IMPROVEMENT_MCP_API_KEY_HEADER",),
    "applicationinsights_connection_string": ("APPLICATIONINSIGHTS_CONNECTION_STRING",),
    "environment": ("ENVIRONMENT",),
    "cosmos_db_endpoint": ("COSMOS_DB_ENDPOINT",),
    "fabric_sql_endpoint": ("FABRIC_SQL_ENDPOINT",),
    "fabric_lakehouse_database": ("FABRIC_LAKEHOUSE_DATABASE", "FABRIC_DATABASE_NAME"),
    "fabric_sales_table": ("FABRIC_SALES_TABLE",),
    "fabric_reviews_table": ("FABRIC_REVIEWS_TABLE",),
    "fabric_data_agent_runtime": ("FABRIC_DATA_AGENT_RUNTIME", "ENABLE_FABRIC_DATA_AGENT_REST"),
    "allowed_origins": ("ALLOWED_ORIGINS",),
    "content_understanding_endpoint": ("CONTENT_UNDERSTANDING_ENDPOINT",),
    "speech_service_endpoint": ("SPEECH_SERVICE_ENDPOINT",),
    "speech_service_region": ("SPEECH_SERVICE_REGION",),
    "logic_app_callback_url": ("LOGIC_APP_CALLBACK_URL",),
    "manager_approval_trigger_url": ("MANAGER_APPROVAL_TRIGGER_URL",),
    "public_app_base_url": ("PUBLIC_APP_BASE_URL",),
    "fabric_data_agent_url": ("FABRIC_DATA_AGENT_URL",),
    "image_project_endpoint_mai": ("IMAGE_PROJECT_ENDPOINT_MAI",),
    "gpt_image_15_deployment_name": ("GPT_IMAGE_15_DEPLOYMENT_NAME",),
    "gpt_image_2_deployment_name": ("GPT_IMAGE_2_DEPLOYMENT_NAME",),
    "marketing_plan_runtime": ("MARKETING_PLAN_RUNTIME",),
    "marketing_plan_prompt_agent_name": ("MARKETING_PLAN_PROMPT_AGENT_NAME",),
    "work_iq_runtime": ("WORKIQ_RUNTIME",),
    "enable_github_copilot_review_agent": ("ENABLE_GITHUB_COPILOT_REVIEW_AGENT",),
    "enable_model_router": ("ENABLE_MODEL_ROUTER", "MODEL_ROUTER_ENABLED"),
    "model_router_endpoint": ("MODEL_ROUTER_ENDPOINT", "AZURE_AI_MODEL_ROUTER_ENDPOINT"),
    "model_router_deployment_name": ("MODEL_ROUTER_DEPLOYMENT_NAME", "MODEL_ROUTER_MODEL_NAME"),
    "model_deployment_allowlist": ("MODEL_DEPLOYMENT_ALLOWLIST", "ALLOWED_MODEL_DEPLOYMENTS"),
    "enable_gpt_55": ("ENABLE_GPT_55", "GPT_55_AVAILABLE"),
    "gpt_55_deployment_name": ("GPT_55_DEPLOYMENT_NAME", "GPT_5_5_DEPLOYMENT_NAME"),
    "enable_foundry_tracing": ("ENABLE_FOUNDRY_TRACING", "FOUNDRY_TRACING_ENABLED"),
    "enable_continuous_monitoring": (
        "ENABLE_CONTINUOUS_MONITORING",
        "CONTINUOUS_MONITORING_ENABLED",
        "ENABLE_CONTINUOUS_EVALUATIONS",
    ),
    "continuous_monitoring_sample_rate": ("CONTINUOUS_MONITORING_SAMPLE_RATE",),
    "enable_evaluation_logging": ("ENABLE_EVALUATION_LOGGING", "EVALUATION_LOGGING_ENABLED"),
    "evaluation_log_retention_days": ("EVALUATION_LOG_RETENTION_DAYS",),
    "enable_cost_metrics": ("ENABLE_COST_METRICS", "COST_METRICS_ENABLED"),
    "mcp_registry_endpoint": ("MCP_REGISTRY_ENDPOINT", "MCP_REGISTRY_URL"),
    "enable_source_ingestion": ("ENABLE_SOURCE_INGESTION", "SOURCE_INGESTION_ENABLED"),
    "source_ingestion_endpoint": ("SOURCE_INGESTION_ENDPOINT", "SOURCE_INGESTION_URL"),
    "source_max_items_per_owner": ("SOURCE_MAX_ITEMS_PER_OWNER",),
    "source_ttl_seconds": ("SOURCE_TTL_SECONDS",),
    "source_max_text_chars": ("SOURCE_MAX_TEXT_CHARS",),
    "source_max_pdf_bytes": ("SOURCE_MAX_PDF_BYTES",),
    "source_max_audio_seconds": ("SOURCE_MAX_AUDIO_SECONDS",),
    "source_max_audio_bytes": ("SOURCE_MAX_AUDIO_BYTES",),
    "enable_voice_talk_to_start": ("ENABLE_VOICE_TALK_TO_START", "VOICE_TALK_TO_START_ENABLED"),
    "enable_mai_transcribe_1": ("ENABLE_MAI_TRANSCRIBE_1", "MAI_TRANSCRIBE_1_ENABLED"),
    "mai_transcribe_1_endpoint": ("MAI_TRANSCRIBE_1_ENDPOINT",),
    "mai_transcribe_1_deployment_name": ("MAI_TRANSCRIBE_1_DEPLOYMENT_NAME",),
    "mai_transcribe_1_api_path": ("MAI_TRANSCRIBE_1_API_PATH",),
    "trust_auth_header_claims": ("TRUST_AUTH_HEADER_CLAIMS",),
    "trusted_auth_header_name": ("TRUSTED_AUTH_HEADER_NAME",),
    "trusted_auth_header_value": ("TRUSTED_AUTH_HEADER_VALUE",),
    "require_authenticated_owner": ("REQUIRE_AUTHENTICATED_OWNER",),
}

# デフォルト値（オプショナルな設定のみ）
_DEFAULTS: dict[str, str] = {
    "model_name": "gpt-5-4-mini",
    "work_iq_timeout_seconds": "120",
    "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
    "environment": "development",
    "fabric_lakehouse_database": "Travel_Lakehouse",
    "fabric_sales_table": "sales_results",
    "fabric_reviews_table": "customer_reviews",
    "fabric_data_agent_runtime": "sql",
    "allowed_origins": "http://localhost:5173",
    "gpt_image_15_deployment_name": "gpt-image-1.5",
    "gpt_image_2_deployment_name": "gpt-image-2",
    "marketing_plan_runtime": "foundry_preprovisioned",
    "marketing_plan_prompt_agent_name": "travel-marketing-plan",
    "work_iq_runtime": "foundry_tool",
    "enable_github_copilot_review_agent": "false",
    "enable_model_router": "false",
    "enable_gpt_55": "false",
    "enable_foundry_tracing": "false",
    "enable_continuous_monitoring": "false",
    "continuous_monitoring_sample_rate": "0.1",
    "enable_evaluation_logging": "false",
    "evaluation_log_retention_days": "30",
    "enable_cost_metrics": "false",
    "enable_source_ingestion": "false",
    "source_max_items_per_owner": "20",
    "source_ttl_seconds": "604800",
    "source_max_text_chars": "20000",
    "source_max_pdf_bytes": "10485760",
    "source_max_audio_seconds": "1800",
    "source_max_audio_bytes": "26214400",
    "enable_voice_talk_to_start": "false",
    "enable_mai_transcribe_1": "false",
    "trust_auth_header_claims": "false",
    "require_authenticated_owner": "false",
}

_PRODUCTION_ENVIRONMENTS = {"production", "prod", "staging"}


@lru_cache(maxsize=1)
def _get_azd_env_values() -> dict[str, str]:
    """azd env get-values の結果を 1 回だけ読み込む。"""
    azd_path = next(
        (resolved for candidate in ("azd", "azd.exe", "azd.cmd", "azd.bat") if (resolved := shutil.which(candidate))),
        None,
    )
    if not azd_path:
        return {}

    try:
        result = subprocess.run(
            [azd_path, "env", "get-values"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}

    if result.returncode != 0:
        return {}

    env: dict[str, str] = {}
    for line in result.stdout.strip().splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"')
    return env


def _resolve_setting(setting_key: str, env_keys: tuple[str, ...], azd_env: dict[str, str]) -> str:
    """process env → azd env → default の順で設定値を解決する。"""
    for name in env_keys:
        value = os.environ.get(name, "")
        if value:
            return value
    for name in env_keys:
        value = azd_env.get(name, "")
        if value:
            return value
    return _DEFAULTS.get(setting_key, "")


def get_settings() -> AppSettings:
    """環境変数から AppSettings をロードする。未設定の必須項目は空文字列になる。"""
    azd_env = _get_azd_env_values()
    settings: dict[str, str] = {}
    for setting_key, env_keys in _ENV_CANDIDATES.items():
        settings[setting_key] = _resolve_setting(setting_key, env_keys, azd_env)
    return AppSettings(**settings)  # type: ignore[typeddict-item]


def is_production_environment() -> bool:
    """本番相当環境かどうかを返す。"""
    environment = _resolve_setting("environment", _ENV_CANDIDATES["environment"], _get_azd_env_values()).lower()
    return environment in _PRODUCTION_ENVIRONMENTS


def get_missing_required_settings() -> list[str]:
    """現在の環境で不足している必須設定の環境変数名を返す。"""
    required: list[str] = []
    if is_production_environment():
        required.append("AZURE_AI_PROJECT_ENDPOINT")
    azd_env = _get_azd_env_values()
    return [name for name in required if not os.environ.get(name) and not azd_env.get(name)]
