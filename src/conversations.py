"""会話履歴の永続化。Cosmos DB またはインメモリ辞書にフォールバックする。"""

import asyncio
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# インメモリストア（Cosmos DB 未設定時のフォールバック）
_memory_store: dict[str, dict] = {}
_conversation_locks: dict[str, asyncio.Lock] = {}
_DEFAULT_OWNER_ID = "anonymous"

# Cosmos DB クライアントのシングルトン（接続プーリングを再利用するため）
_cosmos_client = None
_cosmos_initialized = False


def _normalize_owner_id(owner_id: str | None) -> str:
    """未指定 owner を安全な既定値へ正規化する。"""
    normalized = str(owner_id).strip() if owner_id is not None else ""
    return normalized or _DEFAULT_OWNER_ID


def _build_memory_key(owner_id: str, document_id: str) -> str:
    """インメモリ保存用の複合キーを返す。"""
    return f"{owner_id}:{document_id}"


def _get_owner_id_from_document(doc: dict | None) -> str:
    """保存済み会話ドキュメントから owner_id を取得する。"""
    if not isinstance(doc, dict):
        return _DEFAULT_OWNER_ID
    return _normalize_owner_id(str(doc.get("user_id", "")))


def _get_conversation_lock(conversation_id: str, owner_id: str) -> asyncio.Lock:
    """会話ごとの保存処理を直列化するロックを返す。"""
    lock_key = _build_memory_key(owner_id, conversation_id)
    lock = _conversation_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _conversation_locks[lock_key] = lock
    return lock


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
    owner_id: str | None = None,
) -> None:
    """会話をストアに保存する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    async with _get_conversation_lock(conversation_id, resolved_owner_id):
        existing = await get_conversation(conversation_id, owner_id=resolved_owner_id, allow_cross_owner=owner_id is None)
        document_owner_id = _get_owner_id_from_document(existing) if existing else resolved_owner_id
        doc = _build_conversation_doc(
            conversation_id=conversation_id,
            existing=existing,
            user_input=user_input,
            events=events,
            artifacts=artifacts,
            metrics=metrics,
            status=status,
            owner_id=document_owner_id,
        )
        await _persist_conversation_doc(doc)


async def append_conversation_events(
    conversation_id: str,
    user_input: str | None,
    new_events: list[dict],
    artifacts: dict | None = None,
    metrics: dict | None = None,
    status: str | None = None,
    owner_id: str | None = None,
) -> dict | None:
    """既存会話へイベントを追記しつつ保存する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    async with _get_conversation_lock(conversation_id, resolved_owner_id):
        existing = await get_conversation(conversation_id, owner_id=resolved_owner_id, allow_cross_owner=owner_id is None)
        existing_messages = existing.get("messages", []) if existing else []
        if not isinstance(existing_messages, list):
            existing_messages = []

        resolved_user_input = user_input
        if resolved_user_input is None:
            resolved_user_input = str(existing.get("input", "")) if existing else ""

        resolved_status = status or str(existing.get("status", "completed")) if existing else "completed"
        doc = _build_conversation_doc(
            conversation_id=conversation_id,
            existing=existing,
            user_input=resolved_user_input,
            events=[*existing_messages, *new_events],
            artifacts=artifacts,
            metrics=metrics,
            status=resolved_status,
            owner_id=_get_owner_id_from_document(existing) if existing else resolved_owner_id,
        )
        await _persist_conversation_doc(doc)
        return doc


def _build_conversation_doc(
    conversation_id: str,
    existing: dict | None,
    user_input: str,
    events: list[dict],
    artifacts: dict | None,
    metrics: dict | None,
    status: str,
    owner_id: str,
) -> dict:
    """保存用の会話ドキュメントを構築する。"""
    now = datetime.now(timezone.utc).isoformat()

    existing_artifacts = existing.get("artifacts", []) if existing else []
    if not isinstance(existing_artifacts, list):
        existing_artifacts = [existing_artifacts] if existing_artifacts else []

    new_artifact = dict(artifacts) if artifacts else {}
    if new_artifact:
        new_artifact["version"] = len(existing_artifacts) + 1
        new_artifact["created_at"] = now
        artifact_versions = [*existing_artifacts, new_artifact]
    else:
        artifact_versions = existing_artifacts

    existing_metadata = existing.get("metadata", {}) if existing else {}
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    merged_metadata = {**existing_metadata, **(metrics or {})}

    return {
        "id": conversation_id,
        "user_id": _normalize_owner_id(owner_id),
        "created_at": existing.get("created_at", now) if existing else now,
        "updated_at": now,
        "status": status,
        "input": user_input,
        "messages": events,
        "artifacts": artifact_versions,
        "metadata": merged_metadata,
    }


async def _persist_conversation_doc(doc: dict) -> None:
    """会話ドキュメントを実ストアへ保存する。"""
    conversation_id = str(doc.get("id", ""))
    owner_id = _get_owner_id_from_document(doc)
    container = _get_container()
    if container:
        try:
            await asyncio.to_thread(container.upsert_item, doc)
            logger.info("会話 %s を Cosmos DB に保存", conversation_id)
            return
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB への保存に失敗、インメモリにフォールバック: %s", exc)
        except Exception as exc:
            logger.exception("Cosmos DB への保存で予期しないエラー、インメモリにフォールバック: %s", exc)

    _memory_store[_build_memory_key(owner_id, conversation_id)] = doc
    logger.info("会話 %s をインメモリに保存", conversation_id)


async def get_conversation(
    conversation_id: str,
    owner_id: str | None = None,
    *,
    allow_cross_owner: bool = False,
) -> dict | None:
    """会話を取得する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    container = _get_container()
    if container:
        try:
            if allow_cross_owner and owner_id is None:
                items = await asyncio.to_thread(
                    list,
                    container.query_items(
                        query="SELECT * FROM c WHERE c.id = @id",
                        parameters=[{"name": "@id", "value": conversation_id}],
                        enable_cross_partition_query=True,
                    ),
                )
                for item in items:
                    if isinstance(item, dict):
                        return item
                return None

            result = await asyncio.to_thread(
                container.read_item,
                item=conversation_id,
                partition_key=resolved_owner_id,
            )
            return result if isinstance(result, dict) else None
        except (ValueError, OSError) as exc:
            logger.debug("Cosmos DB から会話 %s が見つからない: %s", conversation_id, exc)
            return None
        except Exception as exc:
            logger.debug("Cosmos DB から会話 %s の取得で予期しないエラー: %s", conversation_id, exc)
            return None

    if allow_cross_owner and owner_id is None:
        for doc in _memory_store.values():
            if isinstance(doc, dict) and str(doc.get("id", "")) == conversation_id and doc.get("type") != "replay":
                return doc
        return None

    doc = _memory_store.get(_build_memory_key(resolved_owner_id, conversation_id))
    if isinstance(doc, dict) and doc.get("type") != "replay":
        return doc
    return None


async def list_conversations(owner_id: str | None = None, limit: int = 20) -> list[dict]:
    """会話一覧を取得する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    container = _get_container()
    if container:
        try:
            query = (
                "SELECT c.id, c.input, c.status, c.created_at FROM c ORDER BY c.created_at DESC OFFSET 0 LIMIT @limit"
            )
            items = await asyncio.to_thread(
                list,
                container.query_items(
                    query=query,
                    parameters=[{"name": "@limit", "value": limit}],
                    partition_key=resolved_owner_id,
                ),
            )
            return items
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB からの一覧取得に失敗: %s", exc)
            return []
        except Exception as exc:
            logger.exception("Cosmos DB からの一覧取得で予期しないエラー: %s", exc)
            return []

    filtered_items = [
        doc
        for doc in _memory_store.values()
        if isinstance(doc, dict) and doc.get("type") != "replay" and _get_owner_id_from_document(doc) == resolved_owner_id
    ]
    return sorted(filtered_items, key=lambda x: x.get("created_at", ""), reverse=True)[:limit]


async def save_replay_data(
    conversation_id: str,
    events_with_timing: list[dict],
    owner_id: str | None = None,
) -> None:
    """リプレイ用の SSE イベントデータをタイムスタンプ付きで保存する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    container = _get_container()
    replay_doc = {
        "id": f"replay-{conversation_id}",
        "user_id": resolved_owner_id,
        "type": "replay",
        "conversation_id": conversation_id,
        "events": events_with_timing,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    if container:
        try:
            await asyncio.to_thread(container.upsert_item, replay_doc)
            return
        except (ValueError, OSError) as exc:
            logger.warning("Cosmos DB へのリプレイデータ保存に失敗: %s", exc)
        except Exception as exc:
            logger.exception("Cosmos DB へのリプレイデータ保存で予期しないエラー: %s", exc)

    _memory_store[_build_memory_key(resolved_owner_id, f"replay-{conversation_id}")] = replay_doc


async def get_replay_data(
    conversation_id: str,
    owner_id: str | None = None,
    *,
    allow_cross_owner: bool = False,
) -> list[dict] | None:
    """リプレイ用の SSE イベントデータを取得する。"""
    resolved_owner_id = _normalize_owner_id(owner_id)
    container = _get_container()
    if container:
        try:
            if allow_cross_owner and owner_id is None:
                items = await asyncio.to_thread(
                    list,
                    container.query_items(
                        query="SELECT * FROM c WHERE c.id = @id",
                        parameters=[{"name": "@id", "value": f"replay-{conversation_id}"}],
                        enable_cross_partition_query=True,
                    ),
                )
                doc = next((item for item in items if isinstance(item, dict)), None)
            else:
                doc = await asyncio.to_thread(
                    container.read_item,
                    item=f"replay-{conversation_id}",
                    partition_key=resolved_owner_id,
                )
            if isinstance(doc, dict):
                return doc.get("events", [])
            return None
        except (ValueError, OSError) as exc:
            logger.debug("Cosmos DB からリプレイデータ取得失敗: %s", exc)
        except Exception as exc:
            logger.debug("Cosmos DB からリプレイデータ取得で予期しないエラー: %s", exc)

    if allow_cross_owner and owner_id is None:
        doc = next(
            (
                value
                for value in _memory_store.values()
                if isinstance(value, dict) and str(value.get("id", "")) == f"replay-{conversation_id}"
            ),
            None,
        )
    else:
        doc = _memory_store.get(_build_memory_key(resolved_owner_id, f"replay-{conversation_id}"))
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
