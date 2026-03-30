"""AzureOpenAIResponsesClient のシングルトンキャッシュ。

毎リクエストで Credential + Client を再生成するオーバーヘッドを削減する。
deployment_name をキーにキャッシュし、異なるモデル設定でも既存クライアントを再利用する。
"""

import logging

from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)

# モジュールレベルのシングルトン
_credential: DefaultAzureCredential | None = None
_clients: dict[str, object] = {}


def get_shared_credential() -> DefaultAzureCredential:
    """共有 DefaultAzureCredential を返す。トークンキャッシュがインスタンス単位で効く。"""
    global _credential
    if _credential is None:
        _credential = DefaultAzureCredential()
    return _credential


def get_responses_client(deployment_name: str | None = None):
    """AzureOpenAIResponsesClient のキャッシュ済みインスタンスを返す。"""
    settings = get_settings()
    deployment = deployment_name or settings["model_name"]

    if deployment in _clients:
        return _clients[deployment]

    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=get_shared_credential(),
        deployment_name=deployment,
    )
    _clients[deployment] = client
    logger.info("AzureOpenAIResponsesClient キャッシュ: deployment=%s", deployment)
    return client
