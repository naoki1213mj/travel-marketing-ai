"""会話履歴・リプレイ API エンドポイント。"""

import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from src.api.chat import limiter
from src.conversations import get_conversation, get_replay_data, list_conversations
from src.request_identity import RequestIdentityError, extract_request_identity
from src.work_iq_session import (
    CONVERSATION_SETTINGS_METADATA_KEY,
    WORK_IQ_SESSION_METADATA_KEY,
    sanitize_conversation_settings,
    sanitize_work_iq_session_for_response,
)

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


def _redact_manager_approval_urls(events: list) -> list:
    """永続化済みイベントから workflow 配信の manager_approval_url(token 入り)を除去する。

    申請者(=会話 owner)へ上司承認 token を再配布して自己承認させないための read-time 防御。
    fix 前に保存された会話にも後方互換で効く。manual 配信のリンクは frontend が共有 UI で
    利用するため温存する。元の events を破壊的に変更せず、必要な要素だけ複製する。
    """
    safe_events: list = []
    for event in events:
        if (
            isinstance(event, dict)
            and event.get("event") == "approval_request"
            and isinstance(event.get("data"), dict)
            and event["data"].get("manager_delivery_mode") != "manual"
            and "manager_approval_url" in event["data"]
        ):
            safe_data = {key: value for key, value in event["data"].items() if key != "manager_approval_url"}
            safe_events.append({**event, "data": safe_data})
        else:
            safe_events.append(event)
    return safe_events


def _sanitize_conversation_document(doc: dict) -> dict:
    """フロントエンドへ返す会話ドキュメントから機密 metadata を除去する。"""
    sanitized = {key: value for key, value in doc.items() if key != "user_id"}
    metadata = sanitized.get("metadata")
    if isinstance(metadata, dict):
        safe_metadata: dict[str, object] = {}
        for key, value in metadata.items():
            if key in _SENSITIVE_METADATA_KEYS:
                continue
            if key == CONVERSATION_SETTINGS_METADATA_KEY:
                safe_metadata[key] = sanitize_conversation_settings(value)
                continue
            if key == WORK_IQ_SESSION_METADATA_KEY:
                session = sanitize_work_iq_session_for_response(value)
                if session is not None:
                    safe_metadata[key] = session
                continue
            safe_metadata[key] = value
        sanitized["metadata"] = safe_metadata
    messages = sanitized.get("messages")
    if isinstance(messages, list):
        sanitized["messages"] = _redact_manager_approval_urls(messages)
    return sanitized


def _sanitize_conversation_list_item(doc: dict) -> dict[str, str]:
    """会話一覧用に詳細 metadata / messages を含まない要約だけを返す。"""
    return {
        "id": str(doc.get("id", "")),
        "input": str(doc.get("input", "")),
        "status": str(doc.get("status", "")),
        "created_at": str(doc.get("created_at", "")),
    }


def _identity_error_response(exc: RequestIdentityError) -> JSONResponse:
    """owner 境界の認証エラーを JSON API レスポンスへ変換する。"""
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "code": exc.code})


@router.get("/conversations")
@limiter.limit("60/minute")
async def conversations_list(request: Request, limit: int = 20) -> Response:
    """会話一覧を取得する。"""
    try:
        identity = extract_request_identity(request, enforce_owner_boundary=True)
    except RequestIdentityError as exc:
        return _identity_error_response(exc)
    items = await list_conversations(owner_id=identity["user_id"], limit=limit)
    safe_items = [_sanitize_conversation_list_item(item) for item in items if isinstance(item, dict)]
    etag = _build_conversations_list_etag(safe_items)
    cache_headers = {
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "ETag": etag,
    }
    if _if_none_match_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=cache_headers)
    return JSONResponse(
        content={"conversations": safe_items},
        headers=cache_headers,
    )


@router.get("/conversations/{conversation_id}")
@limiter.limit("120/minute")
async def conversation_detail(conversation_id: str, request: Request) -> Response:
    """会話詳細を取得する。"""
    try:
        identity = extract_request_identity(request, enforce_owner_boundary=True)
    except RequestIdentityError as exc:
        return _identity_error_response(exc)
    doc = await get_conversation(conversation_id, owner_id=identity["user_id"])
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
@limiter.limit("10/minute")
async def replay(request: Request, conversation_id: str, speed: float = Query(5.0, gt=0.0)) -> StreamingResponse:
    """録画済み SSE イベントを高速リプレイする。

    Args:
        conversation_id: リプレイする会話のID
        speed: リプレイ速度の倍率（デフォルト 5倍速）
    """
    try:
        identity = extract_request_identity(request, enforce_owner_boundary=True)
    except RequestIdentityError as exc:
        error_message = exc.message
        error_code = exc.code
        status_code = exc.status_code

        async def identity_error():
            yield f"event: error\ndata: {json.dumps({'message': error_message, 'code': error_code}, ensure_ascii=False)}\n\n"

        return StreamingResponse(identity_error(), status_code=status_code, media_type="text/event-stream")
    events = await get_replay_data(conversation_id, owner_id=identity["user_id"])
    if isinstance(events, list):
        events = _redact_manager_approval_urls(events)

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
