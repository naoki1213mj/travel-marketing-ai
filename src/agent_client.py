"""AzureOpenAIResponsesClient のシングルトンキャッシュ。

毎リクエストで Credential + Client を再生成するオーバーヘッドを削減する。
deployment_name をキーにキャッシュし、異なるモデル設定でも既存クライアントを再利用する。

APIM_GATEWAY_URL が設定されている場合はモデル呼び出しを APIM AI Gateway 経由に
ルーティングし、トークン制限・メトリクス・監視の恩恵を受ける。
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

    APIM_GATEWAY_URL が設定されている場合は APIM AI Gateway 経由でモデルを呼び出す。
    未設定時は project_endpoint に直接接続する（従来動作）。
    """
    settings = get_settings()
    deployment = deployment_name or settings["model_name"]

    if deployment in _clients:
        return _clients[deployment]

    apim_url = settings.get("apim_gateway_url", "")
    if apim_url:
        # APIM AI Gateway 経由: endpoint に APIM の gateway URL を指定
        # APIM の foundry-ai-gateway API は subscription key 不要、MI 認証のみ
        client = AzureOpenAIResponsesClient(
            endpoint=apim_url,
            credential=get_shared_credential(),
            deployment_name=deployment,
        )
        _clients[deployment] = client
        logger.info(
            "AzureOpenAIResponsesClient キャッシュ (APIM 経由): deployment=%s, apim=%s",
            deployment,
            apim_url,
        )
    else:
        # 直接接続: project_endpoint を使用（従来動作）
        client = AzureOpenAIResponsesClient(
            project_endpoint=settings["project_endpoint"],
            credential=get_shared_credential(),
            deployment_name=deployment,
        )
        _clients[deployment] = client
        logger.info("AzureOpenAIResponsesClient キャッシュ (直接): deployment=%s", deployment)
    return client
