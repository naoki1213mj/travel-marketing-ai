"""会話履歴・リプレイ API エンドポイント。"""

import asyncio
import json
import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse

from src.conversations import get_conversation, get_replay_data, list_conversations

router = APIRouter(prefix="/api", tags=["conversations"])
logger = logging.getLogger(__name__)


@router.get("/conversations")
async def conversations_list(limit: int = 20) -> JSONResponse:
    """会話一覧を取得する。"""
    items = await list_conversations(limit=limit)
    return JSONResponse(content={"conversations": items})


@router.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str) -> JSONResponse:
    """会話詳細を取得する。"""
    doc = await get_conversation(conversation_id)
    if not doc:
        return JSONResponse(status_code=404, content={"error": "conversation not found"})
    return JSONResponse(content=doc)


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
