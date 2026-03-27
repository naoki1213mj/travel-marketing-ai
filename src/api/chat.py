"""SSE チャットエンドポイント。Workflow の結果を SSE ストリームで返す。"""

import asyncio
import json
import logging
import time
import uuid
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.middleware import analyze_content, check_prompt_shield

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)


# --- SSE イベント定義 ---

class SSEEventType(StrEnum):
    """SSE イベント種別（§3.4 準拠）"""

    AGENT_PROGRESS = "agent_progress"
    TOOL_EVENT = "tool_event"
    TEXT = "text"
    IMAGE = "image"
    APPROVAL_REQUEST = "approval_request"
    SAFETY = "safety"
    ERROR = "error"
    DONE = "done"


def format_sse(event_type: str, data: dict) -> str:
    """SSE フォーマットに変換する"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# --- リクエスト / レスポンスモデル ---

class ChatRequest(BaseModel):
    """チャットリクエスト"""

    message: str
    conversation_id: str | None = None


class ApproveRequest(BaseModel):
    """承認/修正リクエスト"""

    conversation_id: str
    response: str


# --- モック SSE ジェネレーター（Phase 1: Azure 未接続のデモ用） ---

async def mock_event_generator(user_input: str, conversation_id: str):
    """デモ用のモック SSE イベントを生成する。Phase 2 以降で実 Workflow に置き換える。"""

    # Agent1: データ検索
    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "data-search-agent",
        "status": "running",
        "step": 1,
        "total_steps": 4,
    })
    await asyncio.sleep(0.5)

    yield format_sse(SSEEventType.TOOL_EVENT, {
        "tool": "search_sales_history",
        "status": "completed",
        "agent": "data-search-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.TEXT, {
        "content": "## データ分析サマリ\n\n沖縄エリアの春季売上は前年比 **+12%** で推移。"
        "ファミリー層が全体の 45% を占め、特に 3〜4 月の需要が高い傾向です。",
        "agent": "data-search-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "data-search-agent",
        "status": "completed",
        "step": 1,
        "total_steps": 4,
    })

    # Agent2: 施策生成
    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "marketing-plan-agent",
        "status": "running",
        "step": 2,
        "total_steps": 4,
    })
    await asyncio.sleep(0.5)

    yield format_sse(SSEEventType.TOOL_EVENT, {
        "tool": "web_search",
        "status": "completed",
        "agent": "marketing-plan-agent",
    })
    await asyncio.sleep(0.3)

    plan_markdown = (
        "# 春の沖縄ファミリープラン 企画書\n\n"
        "## キャッチコピー案\n"
        "1. 「家族で発見！春色おきなわ」\n"
        "2. 「ちゅら海と笑顔の春休み」\n\n"
        "## ターゲット\n"
        "- 小学生の子ども連れファミリー（30〜40代親）\n\n"
        "## プラン概要\n"
        "- 3泊4日　那覇 → 美ら海水族館 → 古宇利島\n"
        "- 価格帯: 1人あたり 89,800円〜\n"
    )
    yield format_sse(SSEEventType.TEXT, {
        "content": plan_markdown,
        "agent": "marketing-plan-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "marketing-plan-agent",
        "status": "completed",
        "step": 2,
        "total_steps": 4,
    })

    # 承認要求
    yield format_sse(SSEEventType.APPROVAL_REQUEST, {
        "prompt": "上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
        "conversation_id": conversation_id,
        "plan_markdown": plan_markdown,
    })
    # ここでストリームを一旦終了（承認入力を待つ）

    # Agent3: 規制チェック（承認後に実行。Phase 3 で承認フロー統合後に分離）
    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "regulation-check-agent",
        "status": "running",
        "step": 3,
        "total_steps": 4,
    })
    await asyncio.sleep(0.5)

    yield format_sse(SSEEventType.TOOL_EVENT, {
        "tool": "foundry_iq_search",
        "status": "completed",
        "agent": "regulation-check-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.TEXT, {
        "content": "## レギュレーションチェック結果\n\n"
        "✅ 旅行業法: 適合\n"
        "✅ 景品表示法: 適合\n"
        "⚠️ 修正提案: 「最安値」→「お得な価格帯」に変更を推奨\n",
        "agent": "regulation-check-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "regulation-check-agent",
        "status": "completed",
        "step": 3,
        "total_steps": 4,
    })

    # Agent4: 販促物生成
    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "brochure-gen-agent",
        "status": "running",
        "step": 4,
        "total_steps": 4,
    })
    await asyncio.sleep(0.5)

    yield format_sse(SSEEventType.TEXT, {
        "content": "<html><body><h1>春の沖縄ファミリープラン</h1>"
        "<p>家族で発見！春色おきなわ</p></body></html>",
        "agent": "brochure-gen-agent",
        "content_type": "html",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.IMAGE, {
        "url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "alt": "沖縄の美ら海をイメージしたヒーロー画像",
        "agent": "brochure-gen-agent",
    })
    await asyncio.sleep(0.3)

    yield format_sse(SSEEventType.AGENT_PROGRESS, {
        "agent": "brochure-gen-agent",
        "status": "completed",
        "step": 4,
        "total_steps": 4,
    })

    # Content Safety 結果
    yield format_sse(SSEEventType.SAFETY, {
        "hate": 0,
        "self_harm": 0,
        "sexual": 0,
        "violence": 0,
        "status": "safe",
    })

    # 完了
    yield format_sse(SSEEventType.DONE, {
        "conversation_id": conversation_id,
        "metrics": {
            "latency_seconds": 3.2,
            "tool_calls": 4,
            "total_tokens": 2850,
        },
    })


# --- エンドポイント ---

async def workflow_event_generator(user_input: str, conversation_id: str):
    """実際の Workflow を実行して SSE イベントを生成する（Azure 接続時）"""
    start_time = time.monotonic()
    tool_call_count = 0

    # Workflow 構築
    try:
        from src.workflows import create_pipeline_workflow
        workflow = create_pipeline_workflow()
    except Exception as e:
        logger.exception("Workflow 構築に失敗")
        yield format_sse(SSEEventType.ERROR, {
            "message": f"Workflow の構築に失敗しました: {e}",
            "code": "WORKFLOW_BUILD_ERROR",
        })
        return

    # Workflow 実行
    try:
        yield format_sse(SSEEventType.AGENT_PROGRESS, {
            "agent": "pipeline",
            "status": "running",
            "step": 1,
            "total_steps": 4,
        })

        result = await workflow.run(user_input)
        result_text = str(result) if result else ""

        yield format_sse(SSEEventType.TEXT, {
            "content": result_text,
            "agent": "pipeline",
        })

    except Exception as e:
        logger.exception("Workflow 実行中にエラーが発生")
        yield format_sse(SSEEventType.ERROR, {
            "message": f"パイプライン実行中にエラーが発生しました: {e}",
            "code": "WORKFLOW_RUNTIME_ERROR",
        })
        return

    # 出力 Content Safety チェック（層4: Text Analysis）
    safety_scores = await analyze_content(result_text)
    yield format_sse(SSEEventType.SAFETY, {
        "hate": safety_scores.hate,
        "self_harm": safety_scores.self_harm,
        "sexual": safety_scores.sexual,
        "violence": safety_scores.violence,
        "status": "safe" if all(
            v == 0 for v in [safety_scores.hate, safety_scores.self_harm,
                             safety_scores.sexual, safety_scores.violence]
        ) else "warning",
    })

    # 完了
    elapsed = time.monotonic() - start_time
    yield format_sse(SSEEventType.DONE, {
        "conversation_id": conversation_id,
        "metrics": {
            "latency_seconds": round(elapsed, 1),
            "tool_calls": tool_call_count,
            "total_tokens": 0,
        },
    })


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """チャットメッセージを受け取り、SSE ストリームでパイプライン結果を返す"""
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # 入力 Content Safety チェック（層1: Prompt Shield）
    shield_result = await check_prompt_shield(request.message)

    async def guarded_generator():
        if not shield_result.is_safe:
            yield format_sse(SSEEventType.ERROR, {
                "message": "入力が安全性チェックに失敗しました",
                "code": "PROMPT_SHIELD_BLOCKED",
            })
            return

        # Azure 設定がある場合は実 Workflow、なければモック
        from src.config import get_settings
        settings = get_settings()
        if settings["project_endpoint"]:
            async for event in workflow_event_generator(request.message, conversation_id):
                yield event
        else:
            async for event in mock_event_generator(request.message, conversation_id):
                yield event

    return StreamingResponse(
        guarded_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/{thread_id}/approve")
async def approve(thread_id: str, request: ApproveRequest) -> StreamingResponse:
    """承認/修正レスポンスを受け取り、後続のパイプライン結果を SSE で返す"""

    async def approval_event_generator():
        """承認後の後続処理を SSE イベントとして生成する（モック）"""
        yield format_sse(SSEEventType.AGENT_PROGRESS, {
            "agent": "regulation-check-agent",
            "status": "running",
            "step": 3,
            "total_steps": 4,
        })
        await asyncio.sleep(1.0)
        yield format_sse(SSEEventType.DONE, {
            "conversation_id": thread_id,
            "metrics": {"latency_seconds": 1.0, "tool_calls": 1, "total_tokens": 500},
        })

    return StreamingResponse(
        approval_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
