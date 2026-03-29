"""会話履歴の永続化。Cosmos DB またはインメモリ辞書にフォールバックする。"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# インメモリストア（Cosmos DB 未設定時のフォールバック）
_memory_store: dict[str, dict] = {}


def _get_cosmos_client():
    """Cosmos DB クライアントを取得する。未設定時は None を返す。"""
    endpoint = os.environ.get("COSMOS_DB_ENDPOINT", "")
    if not endpoint:
        return None
    try:
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        return CosmosClient(url=endpoint, credential=credential)
    except ImportError:
        logger.warning("azure-cosmos がインストールされていません")
        return None
    except Exception:
        logger.exception("Cosmos DB クライアントの作成に失敗")
        return None


def _get_container():
    """conversations コンテナを取得する。"""
    client = _get_cosmos_client()
    if not client:
        return None
    try:
        database = client.get_database_client("travel-marketing")
        return database.get_container_client("conversations")
    except Exception:
        logger.exception("Cosmos DB コンテナの取得に失敗")
        return None


async def save_conversation(
    conversation_id: str,
    user_input: str,
    events: list[dict],
    artifacts: dict | None = None,
    metrics: dict | None = None,
) -> None:
    """会話をストアに保存する。"""
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": conversation_id,
        "user_id": "demo-user",
        "created_at": now,
        "updated_at": now,
        "status": "completed",
        "input": user_input,
        "messages": events,
        "artifacts": artifacts or {},
        "metadata": metrics or {},
    }

    container = _get_container()
    if container:
        try:
            container.upsert_item(doc)
            logger.info("会話 %s を Cosmos DB に保存", conversation_id)
            return
        except Exception:
            logger.exception("Cosmos DB への保存に失敗、インメモリにフォールバック")

    _memory_store[conversation_id] = doc
    logger.info("会話 %s をインメモリに保存", conversation_id)


async def get_conversation(conversation_id: str) -> dict | None:
    """会話を取得する。"""
    container = _get_container()
    if container:
        try:
            return container.read_item(item=conversation_id, partition_key="demo-user")
        except Exception:
            logger.debug("Cosmos DB から会話 %s が見つからない", conversation_id)
            return None

    return _memory_store.get(conversation_id)


async def list_conversations(limit: int = 20) -> list[dict]:
    """会話一覧を取得する。"""
    container = _get_container()
    if container:
        try:
            query = (
                "SELECT c.id, c.input, c.status, c.created_at FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
            )
            items = list(
                container.query_items(
                    query=query, parameters=[{"name": "@limit", "value": limit}], partition_key="demo-user"
                )
            )
            return items
        except Exception:
            logger.exception("Cosmos DB からの一覧取得に失敗")
            return []

    return sorted(_memory_store.values(), key=lambda x: x.get("created_at", ""), reverse=True)[:limit]


async def save_replay_data(conversation_id: str, events_with_timing: list[dict]) -> None:
    """リプレイ用の SSE イベントデータをタイムスタンプ付きで保存する。"""
    container = _get_container()
    replay_doc = {
        "id": f"replay-{conversation_id}",
        "user_id": "demo-user",
        "type": "replay",
        "conversation_id": conversation_id,
        "events": events_with_timing,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if container:
        try:
            container.upsert_item(replay_doc)
            return
        except Exception:
            logger.exception("Cosmos DB へのリプレイデータ保存に失敗")

    _memory_store[f"replay-{conversation_id}"] = replay_doc


async def get_replay_data(conversation_id: str) -> list[dict] | None:
    """リプレイ用の SSE イベントデータを取得する。"""
    container = _get_container()
    if container:
        try:
            doc = container.read_item(item=f"replay-{conversation_id}", partition_key="demo-user")
            return doc.get("events", [])
        except Exception:
            pass

    doc = _memory_store.get(f"replay-{conversation_id}")
    if doc:
        return doc.get("events", [])

    # JSON ファイルからフォールバック
    import json
    from pathlib import Path

    replay_file = Path(__file__).resolve().parent.parent / "data" / "demo-replay.json"
    if replay_file.exists():
        try:
            with open(replay_file, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
            return data.get("events", [])
        except Exception:
            logger.exception("リプレイ JSON の読み込みに失敗")

    return None
