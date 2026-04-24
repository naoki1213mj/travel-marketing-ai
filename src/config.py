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
}

# デフォルト値（オプショナルな設定のみ）
_DEFAULTS: dict[str, str] = {
    "model_name": "gpt-5-4-mini",
    "work_iq_timeout_seconds": "120",
    "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
    "environment": "development",
    "allowed_origins": "http://localhost:5173",
    "gpt_image_15_deployment_name": "gpt-image-1.5",
    "gpt_image_2_deployment_name": "gpt-image-2",
    "marketing_plan_runtime": "foundry_preprovisioned",
    "marketing_plan_prompt_agent_name": "travel-marketing-plan",
    "work_iq_runtime": "foundry_tool",
    "enable_github_copilot_review_agent": "false",
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
    except OSError, subprocess.TimeoutExpired:
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
