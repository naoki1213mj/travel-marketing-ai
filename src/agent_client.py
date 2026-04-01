"""AzureOpenAIResponsesClient のシングルトンキャッシュ。

毎リクエストで Credential + Client を再生成するオーバーヘッドを削減する。
deployment_name をキーにキャッシュし、異なるモデル設定でも既存クライアントを再利用する。

APIM AI Gateway は Foundry ポータルで Gateway を有効化することで自動経由される。
アプリコード側での endpoint 差し替えは不要。
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
    """AzureOpenAIResponsesClient のキャッシュ済みインスタンスを返す。

    project_endpoint を使って Foundry Agent Service 経由でモデルを呼び出す。
    APIM AI Gateway はインフラ側で構成済み（接続 + ポリシー Active）だが、
    AzureOpenAIResponsesClient の endpoint を差し替えると Foundry のツール連携
    （Web Search / Knowledge Base / Code Interpreter 等）が使えなくなるため、
    コード側では project_endpoint を直接使用する。
    APIM のトークン監視・メトリクスは Foundry ポータルの AI Gateway 統合で
    将来的に自動経由される（Hosted Agent 化時）。
    """
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
