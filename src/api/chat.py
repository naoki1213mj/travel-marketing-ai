"""SSE チャットエンドポイント。Workflow の結果を SSE ストリームで返す。"""

import asyncio
import json
import logging
import re
import time
import urllib.request
import uuid
from enum import StrEnum

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings
from src.conversations import save_conversation
from src.middleware import analyze_content, check_prompt_shield, check_tool_response

router = APIRouter(prefix="/api", tags=["chat"])
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

_APPROVAL_KEYWORDS = {
    "approve",
    "approved",
    "go",
    "ok",
    "yes",
    "承認",
    "了承",
    "進めて",
    "批准",
    "同意",
}


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


def _is_approval_response(response_text: str) -> bool:
    """承認レスポンスかどうかを言語非依存で判定する。"""
    normalized = response_text.strip().lower()
    return any(keyword in normalized for keyword in _APPROVAL_KEYWORDS)


# 制御文字除去パターン（改行は許可）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")


def _sanitize_text(value: str) -> str:
    """前後空白除去・制御文字除去・空文字拒否の共通バリデーション"""
    value = value.strip()
    value = _CONTROL_CHAR_RE.sub("", value)
    if not value:
        raise ValueError("メッセージが空です")
    return value


# --- リクエスト / レスポンスモデル ---


class ChatRequest(BaseModel):
    """チャットリクエスト"""

    message: str = Field(..., min_length=1, max_length=5000)
    conversation_id: str | None = Field(None, max_length=100)
    settings: dict | None = Field(None, description="モデルパラメータ設定")

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        """前後空白除去・制御文字除去・空文字拒否"""
        return _sanitize_text(v)


class ApproveRequest(BaseModel):
    """承認/修正リクエスト"""

    conversation_id: str = Field(..., max_length=100)
    response: str = Field(..., min_length=1, max_length=5000)

    @field_validator("response")
    @classmethod
    def sanitize_response(cls, v: str) -> str:
        """前後空白除去・制御文字除去・空文字拒否"""
        return _sanitize_text(v)


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


async def _run_single_agent(agent_name: str, agent_step: int, user_input: str, conversation_id: str):
    """個別エージェントを実行して SSE イベントを生成する"""
    from src.agents import (
        create_brochure_gen_agent,
        create_marketing_plan_agent,
        create_regulation_check_agent,
    )

    start_time = time.monotonic()
    agent_map = {
        "marketing-plan-agent": (create_marketing_plan_agent, 2),
        "regulation-check-agent": (create_regulation_check_agent, 3),
        "brochure-gen-agent": (create_brochure_gen_agent, 4),
    }
    create_fn, step = agent_map.get(agent_name, (create_marketing_plan_agent, 2))
    step = agent_step or step

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": agent_name, "status": "running", "step": step, "total_steps": 4},
    )

    try:
        agent = create_fn()
        result = await agent.run(user_input)
        result_text = str(result) if result else ""

        # 層3: ツール応答に対する Prompt Shield チェック
        tool_shield = await check_tool_response(result_text)
        if not tool_shield.is_safe:
            yield format_sse(
                SSEEventType.SAFETY,
                {"status": "blocked", "reason": "tool_response_unsafe", "details": tool_shield.details},
            )
            yield format_sse(
                SSEEventType.ERROR,
                {"message": "ツール応答が安全性チェックに失敗しました", "code": "TOOL_RESPONSE_BLOCKED"},
            )
            return

        yield format_sse(SSEEventType.TEXT, {"content": result_text, "agent": agent_name})
    except Exception:
        logger.exception("エージェント(%s)の実行に失敗", agent_name)
        yield format_sse(
            SSEEventType.ERROR,
            {"message": f"{agent_name} の実行に失敗しました。", "code": "AGENT_RUNTIME_ERROR"},
        )
        return

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": agent_name, "status": "completed", "step": step, "total_steps": 4},
    )

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

    elapsed = time.monotonic() - start_time
    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "metrics": {"latency_seconds": round(elapsed, 1), "tool_calls": 0, "total_tokens": 0},
        },
    )


async def _refine_events(refine_text: str, conversation_id: str):
    """完了後のマルチターン修正リクエストを処理する SSE イベント"""
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

    settings = get_settings()

    if settings["project_endpoint"]:
        async for event in _run_single_agent(target_agent, step, refine_text, conversation_id):
            yield event
    else:
        async for event in _mock_revision_events(refine_text, conversation_id):
            yield event


async def _post_approval_events(user_response: str, conversation_id: str):
    """承認後に Agent3 → Agent4 を実行する SSE イベント"""
    settings = get_settings()

    if not settings["project_endpoint"]:
        async for event in _mock_post_approval_events(conversation_id):
            yield event
        return

    # Agent3: 規制チェック
    agent3_result = ""
    async for event in _run_single_agent("regulation-check-agent", 3, user_response, conversation_id):
        yield event
        # text イベントの content を取得
        if '"agent": "regulation-check-agent"' in event and '"content"' in event:
            try:
                data_line = [line for line in event.split("\n") if line.startswith("data: ")][0]
                data = json.loads(data_line[len("data: ") :])
                agent3_result = data.get("content", "")
            except (IndexError, json.JSONDecodeError) as exc:
                logger.warning("Agent3 結果の SSE パースに失敗: %s", exc)

    # Agent4: 販促物生成
    async for event in _run_single_agent("brochure-gen-agent", 4, agent3_result or user_response, conversation_id):
        yield event

    # 承認後アクション: Logic Apps にコールバック（Teams 通知 + SharePoint 保存）
    await _trigger_logic_app(conversation_id, agent3_result)


async def _trigger_logic_app(conversation_id: str, result_summary: str) -> None:
    """Logic Apps の HTTP トリガーを呼び出して承認後アクションを実行する。

    Teams チャネルへの通知と SharePoint への成果物保存を Logic Apps 側で処理する。
    """
    settings = get_settings()
    callback_url = settings["logic_app_callback_url"]
    if not callback_url:
        logger.info("LOGIC_APP_CALLBACK_URL 未設定。承認後アクションをスキップ")
        return

    payload = json.dumps(
        {
            "conversation_id": conversation_id,
            "status": "approved",
            "summary": result_summary[:2000] if result_summary else "",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            callback_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        await asyncio.to_thread(urllib.request.urlopen, req, timeout=10)
        logger.info("Logic Apps コールバック送信完了: conversation_id=%s", conversation_id)
    except Exception:
        logger.warning("Logic Apps コールバック送信に失敗（非致命的）", exc_info=True)


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

        # Workflow 結果から最終エージェントのテキスト出力を抽出する
        # Agent Framework rc5: Message.contents は list[Content], Content.text に文字列がある
        result_text = ""
        if result is not None:
            messages = []
            try:
                outputs = result.get_outputs()
                for output in outputs:
                    if isinstance(output, list):
                        messages.extend(output)
                    elif hasattr(output, "contents"):
                        messages.append(output)
            except Exception:
                logger.debug("get_outputs() からの結果取得に失敗")

            # Message.contents (list[Content]) → Content.text を連結
            for msg in reversed(messages):
                contents = getattr(msg, "contents", None)
                role = getattr(msg, "role", None)
                if contents and str(role) == "assistant":
                    text_parts = []
                    for c in contents:
                        t = getattr(c, "text", None)
                        if t and isinstance(t, str):
                            text_parts.append(t)
                    combined = "".join(text_parts).strip()
                    if combined:
                        result_text = combined
                        break

            if not result_text:
                result_text = "パイプライン処理が完了しましたが、テキスト結果を取得できませんでした。"

        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": result_text,
                "agent": "pipeline",
            },
        )

    except Exception as exc:
        logger.exception("Workflow 実行中にエラーが発生")
        # エラー詳細をサニタイズして返す（内部情報は含めない）
        error_type = type(exc).__name__
        yield format_sse(
            SSEEventType.ERROR,
            {
                "message": f"パイプライン実行中にエラーが発生しました（{error_type}）。再試行してください。",
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
            "status": "error"
            if safety_scores.check_failed
            else (
                "safe"
                if all(
                    v == 0
                    for v in [
                        safety_scores.hate,
                        safety_scores.self_harm,
                        safety_scores.sexual,
                        safety_scores.violence,
                    ]
                )
                else "warning"
            ),
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
@limiter.limit("10/minute")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """チャットメッセージを受け取り、SSE ストリームでパイプライン結果を返す"""
    conversation_id = body.conversation_id or str(uuid.uuid4())

    # 入力 Content Safety チェック（層1: Prompt Shield）
    shield_result = await check_prompt_shield(body.message)

    async def guarded_generator():
        collected_events: list[dict] = []
        start = time.monotonic()

        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {
                    "message": "入力が安全性チェックに失敗しました",
                    "code": "PROMPT_SHIELD_BLOCKED",
                },
            )
            return

        async def _collect_and_yield(gen):
            async for event in gen:
                # イベントを収集（リプレイ用タイムスタンプ付き）
                try:
                    lines = event.strip().split("\n")
                    ev_type = lines[0].replace("event: ", "") if lines else ""
                    ev_data = json.loads(lines[1].replace("data: ", "")) if len(lines) > 1 else {}
                    collected_events.append(
                        {"time": round(time.monotonic() - start, 2), "event": ev_type, "data": ev_data}
                    )
                except Exception as exc:
                    logger.warning("SSE イベント収集のパースに失敗: %s", exc)
                yield event

        # conversation_id が既存 = マルチターン修正
        if body.conversation_id:
            async for event in _collect_and_yield(_refine_events(body.message, conversation_id)):
                yield event
        else:
            # Azure 設定がある場合は実 Workflow、なければモック
            settings = get_settings()
            if settings["project_endpoint"]:
                async for event in _collect_and_yield(workflow_event_generator(body.message, conversation_id)):
                    yield event
            else:
                async for event in _collect_and_yield(mock_event_generator(body.message, conversation_id)):
                    yield event

        # 会話を非同期で保存（レスポンスには影響しない）
        try:
            await save_conversation(conversation_id, body.message, collected_events)
        except Exception:
            logger.debug("会話保存に失敗（非致命的）")

        # Agent5: 品質レビュー（バックグラウンドで実行、オプショナル）
        settings = get_settings()
        if settings["project_endpoint"] and not body.conversation_id:
            try:
                from src.agents import create_review_agent

                review_agent = create_review_agent()
                if review_agent:
                    # 収集済みテキストイベントを結合してレビュー対象にする
                    review_input = "\n".join(
                        ev.get("data", {}).get("content", "")
                        for ev in collected_events
                        if ev.get("event") == SSEEventType.TEXT
                    )
                    if review_input.strip():
                        review_result = await review_agent.run(review_input)
                        review_text = str(review_result) if review_result else ""
                        if review_text:
                            yield format_sse(
                                SSEEventType.TEXT,
                                {"content": review_text, "agent": "quality-review-agent"},
                            )
            except Exception:
                logger.warning("Agent5 品質レビューの実行に失敗（スキップ）", exc_info=True)

    return StreamingResponse(
        guarded_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/chat/{thread_id}/approve")
@limiter.limit("10/minute")
async def approve(thread_id: str, request: Request, body: ApproveRequest) -> StreamingResponse:
    """承認/修正レスポンスを受け取り、後続のパイプライン結果を SSE で返す"""
    # 入力 Content Safety チェック（承認レスポンスにも適用）
    shield_result = await check_prompt_shield(body.response)
    is_approved = _is_approval_response(body.response)

    async def approval_event_generator():
        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {"message": "入力が安全性チェックに失敗しました", "code": "PROMPT_SHIELD_BLOCKED"},
            )
            return
        if is_approved:
            async for event in _post_approval_events(body.response, thread_id):
                yield event
        else:
            async for event in _refine_events(body.response, thread_id):
                yield event

    return StreamingResponse(
        approval_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
