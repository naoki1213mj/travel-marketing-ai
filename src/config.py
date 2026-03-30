"""アプリケーション設定。TypedDict + load_settings パターンで環境変数をロードする。"""

import os
from typing import TypedDict

from dotenv import load_dotenv

# ローカル開発用に .env を読み込む
load_dotenv()


class AppSettings(TypedDict):
    """アプリケーションの環境変数設定"""

    project_endpoint: str
    model_name: str
    content_safety_endpoint: str
    applicationinsights_connection_string: str
    environment: str
    cosmos_db_endpoint: str
    fabric_sql_endpoint: str
    allowed_origins: str
    content_understanding_endpoint: str
    speech_service_endpoint: str
    speech_service_region: str
    logic_app_callback_url: str
    fabric_data_agent_url: str


# 環境変数名 → AppSettings キーのマッピング
_ENV_MAP: dict[str, str] = {
    "AZURE_AI_PROJECT_ENDPOINT": "project_endpoint",
    "MODEL_NAME": "model_name",
    "CONTENT_SAFETY_ENDPOINT": "content_safety_endpoint",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "applicationinsights_connection_string",
    "ENVIRONMENT": "environment",
    "COSMOS_DB_ENDPOINT": "cosmos_db_endpoint",
    "FABRIC_SQL_ENDPOINT": "fabric_sql_endpoint",
    "ALLOWED_ORIGINS": "allowed_origins",
    "CONTENT_UNDERSTANDING_ENDPOINT": "content_understanding_endpoint",
    "SPEECH_SERVICE_ENDPOINT": "speech_service_endpoint",
    "SPEECH_SERVICE_REGION": "speech_service_region",
    "LOGIC_APP_CALLBACK_URL": "logic_app_callback_url",
    "FABRIC_DATA_AGENT_URL": "fabric_data_agent_url",
}

# デフォルト値（オプショナルな設定のみ）
_DEFAULTS: dict[str, str] = {
    "model_name": "gpt-5-4-mini",
    "environment": "development",
    "allowed_origins": "http://localhost:5173",
}

_PRODUCTION_ENVIRONMENTS = {"production", "prod", "staging"}


def get_settings() -> AppSettings:
    """環境変数から AppSettings をロードする。未設定の必須項目は空文字列になる。"""
    settings: dict[str, str] = {}
    for env_key, setting_key in _ENV_MAP.items():
        value = os.environ.get(env_key, _DEFAULTS.get(setting_key, ""))
        settings[setting_key] = value
    return AppSettings(**settings)  # type: ignore[typeddict-item]


def is_production_environment() -> bool:
    """本番相当環境かどうかを返す。"""
    environment = os.environ.get("ENVIRONMENT", _DEFAULTS["environment"]).lower()
    return environment in _PRODUCTION_ENVIRONMENTS



def get_missing_required_settings() -> list[str]:
    """現在の環境で不足している必須設定の環境変数名を返す。"""
    required: list[str] = []
    if is_production_environment():
        required.extend(["AZURE_AI_PROJECT_ENDPOINT", "CONTENT_SAFETY_ENDPOINT"])
    return [name for name in required if not os.environ.get(name)]
