"""SSE チャットエンドポイント。Workflow の結果を SSE ストリームで返す。"""

import asyncio
import json
import logging
import time
import uuid
from enum import StrEnum

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

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

    message: str = Field(..., min_length=1, max_length=5000)
    conversation_id: str | None = Field(None, max_length=100)


class ApproveRequest(BaseModel):
    """承認/修正リクエスト"""

    conversation_id: str = Field(..., max_length=100)
    response: str = Field(..., min_length=1, max_length=5000)


# --- モック SSE ジェネレーター（Phase 1: Azure 未接続のデモ用） ---


async def mock_event_generator(user_input: str, conversation_id: str):
    """デモ用のモック SSE イベントを生成する。Phase 2 以降で実 Workflow に置き換える。"""

    # Agent1: データ検索
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "data-search-agent",
            "status": "running",
            "step": 1,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "search_sales_history",
            "status": "completed",
            "agent": "data-search-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": "## データ分析サマリ\n\n沖縄エリアの春季売上は前年比 **+12%** で推移。"
            "ファミリー層が全体の 45% を占め、特に 3〜4 月の需要が高い傾向です。",
            "agent": "data-search-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "data-search-agent",
            "status": "completed",
            "step": 1,
            "total_steps": 4,
        },
    )

    # Agent2: 施策生成
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "marketing-plan-agent",
            "status": "running",
            "step": 2,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "web_search",
            "status": "completed",
            "agent": "marketing-plan-agent",
        },
    )
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
    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": plan_markdown,
            "agent": "marketing-plan-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "marketing-plan-agent",
            "status": "completed",
            "step": 2,
            "total_steps": 4,
        },
    )

    # 承認要求（ここでストリーム一旦終了。フロントエンドが approve EP を呼ぶ）
    yield format_sse(
        SSEEventType.APPROVAL_REQUEST,
        {
            "prompt": "上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
            "conversation_id": conversation_id,
            "plan_markdown": plan_markdown,
        },
    )


async def _mock_post_approval_events(conversation_id: str):
    """承認後の Agent3 → Agent4 のモック SSE イベント"""

    # Agent3: 規制チェック
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "regulation-check-agent",
            "status": "running",
            "step": 3,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "check_ng_expressions",
            "status": "completed",
            "agent": "regulation-check-agent",
        },
    )
    await asyncio.sleep(0.2)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "check_travel_law_compliance",
            "status": "completed",
            "agent": "regulation-check-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": "## レギュレーションチェック結果\n\n"
            "✅ 旅行業法: 適合（書面交付義務・広告表示規制を確認済み）\n"
            "✅ 景品表示法: 適合（有利誤認・優良誤認なし）\n"
            "⚠️ 修正提案: 価格表示に「税込」を追記することを推奨\n\n"
            "### 修正済み企画書\n"
            "- 価格帯: 1人あたり **89,800円〜（税込）** に修正\n"
            "- 旅行会社登録番号をフッターに追加\n",
            "agent": "regulation-check-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "regulation-check-agent",
            "status": "completed",
            "step": 3,
            "total_steps": 4,
        },
    )

    # Agent4: 販促物生成
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "brochure-gen-agent",
            "status": "running",
            "step": 4,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "generate_hero_image",
            "status": "completed",
            "agent": "brochure-gen-agent",
        },
    )
    await asyncio.sleep(0.3)

    # HTML ブローシャ
    brochure_html = (
        "<!DOCTYPE html><html lang='ja'><head><meta charset='UTF-8'>"
        "<style>body{font-family:'Noto Sans JP',sans-serif;margin:0;}"
        ".hero{background:linear-gradient(135deg,#0066CC,#00A86B);color:#fff;padding:60px 40px;text-align:center;}"
        ".hero h1{font-size:2em;margin:0 0 10px;}.hero p{font-size:1.2em;opacity:0.9;}"
        ".content{padding:40px;max-width:800px;margin:0 auto;}"
        ".price{background:#f0f9ff;padding:20px;border-radius:8px;margin:20px 0;}"
        ".footer{background:#333;color:#aaa;padding:20px 40px;font-size:0.8em;text-align:center;}"
        "</style></head><body>"
        "<div class='hero'><h1>🌺 春の沖縄ファミリープラン</h1>"
        "<p>家族で発見！春色おきなわ</p></div>"
        "<div class='content'>"
        "<h2>プラン概要</h2>"
        "<p>3泊4日で沖縄の魅力を満喫するファミリー向けプラン。</p>"
        "<ul><li>1日目: 那覇到着 → 国際通り散策</li>"
        "<li>2日目: 美ら海水族館 → 海洋博公園</li>"
        "<li>3日目: 古宇利島 → ハートロック → ビーチ</li>"
        "<li>4日目: 首里城 → 那覇空港</li></ul>"
        "<div class='price'><h3>💰 料金</h3>"
        "<p><strong>1人あたり 89,800円〜（税込）</strong></p>"
        "<p>小学生以下: 59,800円〜（税込）</p></div></div>"
        "<div class='footer'>"
        "<p>株式会社トラベルマーケティング｜観光庁長官登録旅行業第○○○○号</p>"
        "<p>※写真はイメージです</p></div></body></html>"
    )
    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": brochure_html,
            "agent": "brochure-gen-agent",
            "content_type": "html",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "generate_banner_image",
            "status": "completed",
            "agent": "brochure-gen-agent",
        },
    )
    await asyncio.sleep(0.2)

    # ヒーロー画像（SVG data URI — 沖縄の海をイメージ）
    hero_svg = (
        "data:image/svg+xml;charset=utf-8,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='400'%3E"
        "%3Cdefs%3E%3ClinearGradient id='sky' x1='0' y1='0' x2='0' y2='1'%3E"
        "%3Cstop offset='0%25' stop-color='%2387CEEB'/%3E"
        "%3Cstop offset='100%25' stop-color='%23E0F7FF'/%3E"
        "%3C/linearGradient%3E"
        "%3ClinearGradient id='sea' x1='0' y1='0' x2='0' y2='1'%3E"
        "%3Cstop offset='0%25' stop-color='%230099CC'/%3E"
        "%3Cstop offset='100%25' stop-color='%23006699'/%3E"
        "%3C/linearGradient%3E%3C/defs%3E"
        "%3Crect width='800' height='400' fill='url(%23sky)'/%3E"
        "%3Crect y='250' width='800' height='150' fill='url(%23sea)'/%3E"
        "%3Ccircle cx='650' cy='80' r='50' fill='%23FFD700' opacity='0.9'/%3E"
        "%3Cellipse cx='400' cy='250' rx='300' ry='20' fill='%23F5DEB3'/%3E"
        "%3Ctext x='400' y='180' text-anchor='middle' font-size='36' "
        "font-family='sans-serif' fill='%23333' font-weight='bold'%3E"
        "%F0%9F%8C%BA 春の沖縄ファミリープラン%3C/text%3E"
        "%3Ctext x='400' y='220' text-anchor='middle' font-size='18' "
        "font-family='sans-serif' fill='%23555'%3E"
        "家族で発見！春色おきなわ%3C/text%3E"
        "%3C/svg%3E"
    )
    yield format_sse(
        SSEEventType.IMAGE,
        {
            "url": hero_svg,
            "alt": "沖縄の美ら海をイメージしたヒーロー画像",
            "agent": "brochure-gen-agent",
        },
    )
    await asyncio.sleep(0.2)

    # バナー画像（SNS 用）
    banner_svg = (
        "data:image/svg+xml;charset=utf-8,"
        "%3Csvg xmlns='http://www.w3.org/2000/svg' width='1200' height='628'%3E"
        "%3Crect width='1200' height='628' fill='%230066CC'/%3E"
        "%3Crect x='40' y='40' width='1120' height='548' rx='16' "
        "fill='white' opacity='0.95'/%3E"
        "%3Ctext x='600' y='200' text-anchor='middle' font-size='48' "
        "font-family='sans-serif' fill='%230066CC' font-weight='bold'%3E"
        "✈️ 春の沖縄ファミリープラン%3C/text%3E"
        "%3Ctext x='600' y='280' text-anchor='middle' font-size='28' "
        "font-family='sans-serif' fill='%23333'%3E"
        "3泊4日 89,800円〜（税込）%3C/text%3E"
        "%3Ctext x='600' y='340' text-anchor='middle' font-size='22' "
        "font-family='sans-serif' fill='%23FF6B35'%3E"
        "🌺 家族で発見！春色おきなわ%3C/text%3E"
        "%3Crect x='450' y='400' width='300' height='60' rx='30' fill='%23FF6B35'/%3E"
        "%3Ctext x='600' y='440' text-anchor='middle' font-size='20' "
        "font-family='sans-serif' fill='white' font-weight='bold'%3E"
        "詳しくはこちら →%3C/text%3E"
        "%3C/svg%3E"
    )
    yield format_sse(
        SSEEventType.IMAGE,
        {
            "url": banner_svg,
            "alt": "SNS バナー画像（Instagram / Twitter 用）",
            "agent": "brochure-gen-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "brochure-gen-agent",
            "status": "completed",
            "step": 4,
            "total_steps": 4,
        },
    )

    # Content Safety 結果
    yield format_sse(
        SSEEventType.SAFETY,
        {
            "hate": 0,
            "self_harm": 0,
            "sexual": 0,
            "violence": 0,
            "status": "safe",
        },
    )

    # 完了
    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "metrics": {
                "latency_seconds": 4.8,
                "tool_calls": 6,
                "total_tokens": 3200,
            },
        },
    )


async def _mock_revision_events(revision_text: str, conversation_id: str):
    """修正指示を受けて Agent2 を再実行するモック SSE イベント"""

    # Agent2 再実行
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "marketing-plan-agent",
            "status": "running",
            "step": 2,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    revised_plan = (
        "# 春の沖縄ファミリープラン 企画書（修正版）\n\n"
        f"## 修正内容\n> {revision_text}\n\n"
        "## キャッチコピー案（修正後）\n"
        "1. 「わくわく発見！春の沖縄ファミリーアドベンチャー」\n"
        "2. 「ちゅら海で笑顔の春休み」\n"
        "3. 「家族の絆が深まる沖縄 3 泊 4 日」\n\n"
        "## ターゲット\n"
        "- 小学生の子ども連れファミリー（30〜40 代親）\n\n"
        "## プラン概要\n"
        "- 3 泊 4 日　那覇 → 美ら海水族館 → 古宇利島\n"
        "- 価格帯: 1 人あたり 89,800 円〜（税込）\n"
    )
    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": revised_plan,
            "agent": "marketing-plan-agent",
        },
    )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": "marketing-plan-agent",
            "status": "completed",
            "step": 2,
            "total_steps": 4,
        },
    )

    # 再度承認要求
    yield format_sse(
        SSEEventType.APPROVAL_REQUEST,
        {
            "prompt": "修正した企画書を確認してください。承認する場合は「承認」、さらに修正したい場合は修正内容を入力してください。",
            "conversation_id": conversation_id,
            "plan_markdown": revised_plan,
        },
    )


async def _mock_refine_events(refine_text: str, conversation_id: str):
    """完了後のマルチターン修正リクエストを処理するモック SSE イベント"""

    # 修正対象を判定（簡易）
    text_lower = refine_text.lower()
    if any(k in text_lower for k in ["画像", "バナー", "イメージ", "色", "明るく"]):
        target_agent = "brochure-gen-agent"
        step = 4
    elif any(k in text_lower for k in ["規制", "法令", "チェック", "表現"]):
        target_agent = "regulation-check-agent"
        step = 3
    else:
        target_agent = "marketing-plan-agent"
        step = 2

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": target_agent,
            "status": "running",
            "step": step,
            "total_steps": 4,
        },
    )
    await asyncio.sleep(0.5)

    if target_agent == "marketing-plan-agent":
        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": f"# 企画書（修正版）\n\n> 修正指示: {refine_text}\n\n"
                "## キャッチコピー案（修正後）\n"
                "1. 「ポップに弾ける！春の沖縄ファミリー旅」\n"
                "2. 「笑顔 100% 沖縄スプリング」\n\n"
                "## プラン概要\n"
                "- 3 泊 4 日　那覇 → 美ら海 → 古宇利島\n"
                "- 価格帯: 1 人あたり 89,800 円〜（税込）\n",
                "agent": "marketing-plan-agent",
            },
        )
    elif target_agent == "brochure-gen-agent":
        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": f"画像を修正しました: {refine_text}",
                "agent": "brochure-gen-agent",
            },
        )
    else:
        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": f"規制チェックを再実行しました: {refine_text}\n\n✅ 全項目適合",
                "agent": "regulation-check-agent",
            },
        )
    await asyncio.sleep(0.3)

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {
            "agent": target_agent,
            "status": "completed",
            "step": step,
            "total_steps": 4,
        },
    )

    yield format_sse(
        SSEEventType.SAFETY,
        {
            "hate": 0,
            "self_harm": 0,
            "sexual": 0,
            "violence": 0,
            "status": "safe",
        },
    )

    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "metrics": {"latency_seconds": 1.5, "tool_calls": 2, "total_tokens": 1200},
        },
    )


# --- エンドポイント ---


async def workflow_event_generator(user_input: str, conversation_id: str):
    """実際の Workflow を実行して SSE イベントを生成する（Azure 接続時）"""
    start_time = time.monotonic()
    tool_call_count = 0

    # Workflow 構築
    try:
        from src.workflows import create_pipeline_workflow

        workflow = create_pipeline_workflow()
    except Exception:
        logger.exception("Workflow 構築に失敗")
        yield format_sse(
            SSEEventType.ERROR,
            {
                "message": "Workflow の構築に失敗しました。再試行してください。",
                "code": "WORKFLOW_BUILD_ERROR",
            },
        )
        return

    # Workflow 実行
    try:
        yield format_sse(
            SSEEventType.AGENT_PROGRESS,
            {
                "agent": "pipeline",
                "status": "running",
                "step": 1,
                "total_steps": 4,
            },
        )

        result = await workflow.run(user_input)
        result_text = str(result) if result else ""

        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": result_text,
                "agent": "pipeline",
            },
        )

    except Exception:
        logger.exception("Workflow 実行中にエラーが発生")
        yield format_sse(
            SSEEventType.ERROR,
            {
                "message": "パイプライン実行中にエラーが発生しました。再試行してください。",
                "code": "WORKFLOW_RUNTIME_ERROR",
            },
        )
        return

    # 出力 Content Safety チェック（層4: Text Analysis）
    safety_scores = await analyze_content(result_text)
    yield format_sse(
        SSEEventType.SAFETY,
        {
            "hate": safety_scores.hate,
            "self_harm": safety_scores.self_harm,
            "sexual": safety_scores.sexual,
            "violence": safety_scores.violence,
            "status": "safe"
            if all(
                v == 0
                for v in [safety_scores.hate, safety_scores.self_harm, safety_scores.sexual, safety_scores.violence]
            )
            else "warning",
        },
    )

    # 完了
    elapsed = time.monotonic() - start_time
    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "metrics": {
                "latency_seconds": round(elapsed, 1),
                "tool_calls": tool_call_count,
                "total_tokens": 0,
            },
        },
    )


@router.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """チャットメッセージを受け取り、SSE ストリームでパイプライン結果を返す"""
    conversation_id = request.conversation_id or str(uuid.uuid4())

    # 入力 Content Safety チェック（層1: Prompt Shield）
    shield_result = await check_prompt_shield(request.message)

    async def guarded_generator():
        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {
                    "message": "入力が安全性チェックに失敗しました",
                    "code": "PROMPT_SHIELD_BLOCKED",
                },
            )
            return

        # conversation_id が既存 = マルチターン修正
        if request.conversation_id:
            async for event in _mock_refine_events(request.message, conversation_id):
                yield event
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
    is_approved = "承認" in request.response

    async def approval_event_generator():
        if is_approved:
            # 承認 → Agent3(規制チェック) → Agent4(販促物生成) を実行
            async for event in _mock_post_approval_events(thread_id):
                yield event
        else:
            # 修正 → Agent2 を再実行して修正版を生成 → 再度承認要求
            async for event in _mock_revision_events(request.response, thread_id):
                yield event

    return StreamingResponse(
        approval_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
