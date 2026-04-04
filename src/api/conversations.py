"""会話履歴・リプレイ API エンドポイント。"""

import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from src.conversations import get_conversation, get_replay_data, list_conversations

router = APIRouter(prefix="/api", tags=["conversations"])
logger = logging.getLogger(__name__)
_SENSITIVE_METADATA_KEYS = {"manager_approval_callback_token"}


def _build_conversation_etag(doc: dict) -> str:
    """会話ドキュメントの軽量 ETag を返す。"""
    updated_at = str(doc.get("updated_at") or doc.get("created_at") or "")
    status = str(doc.get("status") or "")
    message_count = len(doc.get("messages", [])) if isinstance(doc.get("messages"), list) else 0
    artifact_count = len(doc.get("artifacts", [])) if isinstance(doc.get("artifacts"), list) else 0
    basis = f"{doc.get('id', '')}|{updated_at}|{status}|{message_count}|{artifact_count}"
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def _build_conversations_list_etag(items: list[dict]) -> str:
    """会話一覧レスポンスの軽量 ETag を返す。"""
    normalized_items = [
        {
            "id": str(item.get("id", "")),
            "input": str(item.get("input", "")),
            "status": str(item.get("status", "")),
            "created_at": str(item.get("created_at", "")),
        }
        for item in items
    ]
    basis = json.dumps(normalized_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def _if_none_match_matches(header_value: str | None, etag: str) -> bool:
    """If-None-Match に現在の ETag が含まれているかを判定する。"""
    if not header_value:
        return False
    candidates = [value.strip() for value in header_value.split(",") if value.strip()]
    return etag in candidates or "*" in candidates


def _sanitize_conversation_document(doc: dict) -> dict:
    """フロントエンドへ返す会話ドキュメントから機密 metadata を除去する。"""
    sanitized = dict(doc)
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict):
        sanitized["metadata"] = {key: value for key, value in metadata.items() if key not in _SENSITIVE_METADATA_KEYS}
    return sanitized


@router.get("/conversations")
async def conversations_list(limit: int = 20, request: Request | None = None) -> Response:
    """会話一覧を取得する。"""
    items = await list_conversations(limit=limit)
    etag = _build_conversations_list_etag(items)
    cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "ETag": etag,
    }
    if request is not None and _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=cache_headers)
    return JSONResponse(
        content={"conversations": items},
        headers=cache_headers,
    )


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str, request: Request) -> Response:
    """会話詳細を取得する。"""
    doc = await get_conversation(conversation_id)
    if not doc:
        return JSONResponse(status_code=404, content={"error": "conversation not found"})
    etag = _build_conversation_etag(doc)
    cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "ETag": etag,
    }
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=cache_headers)
    return JSONResponse(
        content=_sanitize_conversation_document(doc),
        headers=cache_headers,
    )


@router.get("/replay/{conversation_id}")
async def replay(conversation_id: str, speed: float = Query(5.0, gt=0.0)) -> StreamingResponse:
    """録画済み SSE イベントを高速リプレイする。

    Args:
        conversation_id: リプレイする会話のID
        speed: リプレイ速度の倍率（デフォルト 5倍速）
    """
    events = await get_replay_data(conversation_id)

    if not events:
        # デモ用フォールバック: JSON ファイルがなければ空レスポンス
        async def empty():
            yield f"event: error\ndata: {json.dumps({'message': 'リプレイデータが見つかりません', 'code': 'REPLAY_NOT_FOUND'}, ensure_ascii=False)}\n\n"

        return StreamingResponse(empty(), media_type="text/event-stream")

    async def replay_generator():
        prev_time = 0.0
        for event in events:
            event_time = event.get("time", 0.0)
            delay = max(0, (event_time - prev_time) / speed)
            if delay > 0:
                await asyncio.sleep(delay)
            prev_time = event_time

            event_type = event.get("event", "text")
            data = event.get("data", {})
            yield f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        replay_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
