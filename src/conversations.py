"""会話履歴の永続化。Cosmos DB またはインメモリ辞書にフォールバックする。"""

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# インメモリストア（Cosmos DB 未設定時のフォールバック）
_memory_store: dict[str, dict] = {}

# Cosmos DB クライアントのシングルトン（接続プーリングを再利用するため）
_cosmos_client = None
_cosmos_initialized = False


def _is_demo_replay_request(conversation_id: str) -> bool:
    """デモ用 replay のみ JSON フォールバックを許可する。"""
    return conversation_id.startswith("demo-")


def _get_cosmos_client():
    """Cosmos DB クライアントを取得する。未設定時は None を返す。

    CosmosClient は接続プーリングを内蔵しているため、モジュールレベルで
    シングルトンとして保持し、呼び出しごとの再生成を避ける。
    """
    global _cosmos_client, _cosmos_initialized
    if _cosmos_initialized:
        return _cosmos_client
    _cosmos_initialized = True

    endpoint = os.environ.get("COSMOS_DB_ENDPOINT", "")
    if not endpoint:
        return None
    try:
        from azure.cosmos import CosmosClient
        from azure.identity import DefaultAzureCredential

        _cosmos_client = CosmosClient(url=endpoint, credential=DefaultAzureCredential())
        return _cosmos_client
    except ImportError:
        logger.warning("azure-cosmos がインストールされていません")
        return None
    except (ValueError, OSError) as exc:
        logger.warning("Cosmos DB クライアントの作成に失敗: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Cosmos DB クライアントの作成で予期しないエラー: %s", exc)
        return None


def _get_container():
    """conversations コンテナを取得する。"""
    client = _get_cosmos_client()
    if not client:
        return None
    try:
        database = client.get_database_client("travel-marketing")
        return database.get_container_client("conversations")
    except (ValueError, OSError) as exc:
        logger.warning("Cosmos DB コンテナの取得に失敗: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Cosmos DB コンテナの取得で予期しないエラー: %s", exc)
        return None


async def save_conversation(
    conversation_id: str,
    user_input: str,
    events: list[dict],
    artifacts: dict | None = None,
    metrics: dict | None = None,
    status: str = "completed",
) -> None:
    """会話をストアに保存する。"""
    now = datetime.now(timezone.utc).isoformat()
    existing = await get_conversation(conversation_id)
    # 既存の artifacts バージョン配列を維持しつつ新しいバージョンを追加
    existing_artifacts = existing.get("artifacts", []) if existing else []
    if not isinstance(existing_artifacts, list):
        # フラット dict→配列への移行互換: 旧形式はバージョン 1 として取り込む
        existing_artifacts = [existing_artifacts] if existing_artifacts else []
    new_artifact = artifacts or {}
    if new_artifact:
        new_artifact["version"] = len(existing_artifacts) + 1
        new_artifact["created_at"] = now
        artifact_versions = [*existing_artifacts, new_artifact]
    else:
        artifact_versions = existing_artifacts

    doc = {
        "id": conversation_id,
        "user_id": "demo-user",
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "status": status,
        "input": user_input,
        "messages": events,
        "artifacts": artifact_versions,
        "metadata": metrics or {},
    }

    container = _get_container()
    if container:
        try:
            container.upsert_item(doc)
            logger.info("会話 %s を Cosmos DB に保存", conversation_id)
            return
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB への保存に失敗、インメモリにフォールバック: %s", exc)
        except Exception as exc:
            logger.exception("Cosmos DB への保存で予期しないエラー、インメモリにフォールバック: %s", exc)

    _memory_store[conversation_id] = doc
    logger.info("会話 %s をインメモリに保存", conversation_id)


async def get_conversation(conversation_id: str) -> dict | None:
    """会話を取得する。"""
    container = _get_container()
    if container:
        try:
            return container.read_item(item=conversation_id, partition_key="demo-user")
        except (ValueError, OSError) as exc:
            logger.debug("Cosmos DB から会話 %s が見つからない: %s", conversation_id, exc)
            return None
        except Exception as exc:
            logger.debug("Cosmos DB から会話 %s の取得で予期しないエラー: %s", conversation_id, exc)
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
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB からの一覧取得に失敗: %s", exc)
            return []
        except Exception as exc:
            logger.exception("Cosmos DB からの一覧取得で予期しないエラー: %s", exc)
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
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB へのリプレイデータ保存に失敗: %s", exc)
        except Exception as exc:
            logger.exception("Cosmos DB へのリプレイデータ保存で予期しないエラー: %s", exc)

    _memory_store[f"replay-{conversation_id}"] = replay_doc


async def get_replay_data(conversation_id: str) -> list[dict] | None:
    """リプレイ用の SSE イベントデータを取得する。"""
    container = _get_container()
    if container:
        try:
            doc = container.read_item(item=f"replay-{conversation_id}", partition_key="demo-user")
            return doc.get("events", [])
        except (ValueError, OSError) as exc:
            logger.debug("Cosmos DB からリプレイデータ取得失敗: %s", exc)
        except Exception as exc:
            logger.debug("Cosmos DB からリプレイデータ取得で予期しないエラー: %s", exc)

    doc = _memory_store.get(f"replay-{conversation_id}")
    if doc:
        return doc.get("events", [])

    # JSON ファイルからのフォールバックはデモ replay のみ許可
    if not _is_demo_replay_request(conversation_id):
        return None

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
        except (ValueError, OSError) as exc:
            logger.warning("リプレイ JSON の読み込みに失敗: %s", exc)
        except Exception as exc:
            logger.exception("リプレイ JSON の読み込みで予期しないエラー: %s", exc)

    return None
