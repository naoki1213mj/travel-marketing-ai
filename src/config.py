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


# 環境変数名 → AppSettings キーのマッピング
_ENV_MAP: dict[str, str] = {
    "AZURE_AI_PROJECT_ENDPOINT": "project_endpoint",
    "MODEL_NAME": "model_name",
    "CONTENT_SAFETY_ENDPOINT": "content_safety_endpoint",
    "APPLICATIONINSIGHTS_CONNECTION_STRING": "applicationinsights_connection_string",
}

# デフォルト値（オプショナルな設定のみ）
_DEFAULTS: dict[str, str] = {
    "model_name": "gpt-5.4-mini",
}


def get_settings() -> AppSettings:
    """環境変数から AppSettings をロードする。未設定の必須項目は空文字列になる。"""
    settings: dict[str, str] = {}
    for env_key, setting_key in _ENV_MAP.items():
        value = os.environ.get(env_key, _DEFAULTS.get(setting_key, ""))
        settings[setting_key] = value
    return AppSettings(**settings)  # type: ignore[typeddict-item]
