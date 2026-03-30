"""SSE チャットエンドポイント。Workflow の結果を SSE ストリームで返す。"""

import asyncio
import json
import logging
import random
import re
import time
import urllib.request
import uuid
from enum import StrEnum
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings
from src.conversations import get_conversation, save_conversation
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
_BROCHURE_HTML_BLOCK_RE = re.compile(r"```html\s*(.*?)```", re.IGNORECASE | re.DOTALL)
_DATA_URI_RE = re.compile(r"data:[^\"'\s>]+", re.IGNORECASE)
_PIPELINE_TOTAL_STEPS = 5


class PendingApprovalContext(TypedDict):
    """承認待ちの企画書コンテキスト。"""

    user_input: str
    analysis_markdown: str
    plan_markdown: str
    model_settings: dict | None


class AgentExecutionOutcome(TypedDict):
    """単一エージェント実行の結果。"""

    events: list[str]
    text: str
    success: bool
    latency_seconds: float
    tool_calls: int
    total_tokens: int


_pending_approvals: dict[str, PendingApprovalContext] = {}
_TOOL_EVENT_HINTS: dict[str, list[str]] = {
    "data-search-agent": ["query_data_agent", "search_sales_history", "search_customer_reviews", "code_interpreter"],
    "marketing-plan-agent": ["web_search"],
    "regulation-check-agent": ["search_knowledge_base", "check_ng_expressions", "check_travel_law_compliance"],
    "plan-revision-agent": [],
    "brochure-gen-agent": [
        "generate_hero_image",
        "generate_banner_image",
        "analyze_existing_brochure",
    ],
    "video-gen-agent": ["generate_promo_video"],
}


class _InlineImageExtractor(HTMLParser):
    """HTML 内の data URI 画像を抽出する。"""

    def __init__(self) -> None:
        super().__init__()
        self.images: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "img":
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        src = attr_map.get("src", "")
        if not src.startswith("data:image/"):
            return
        self.images.append({"url": src, "alt": attr_map.get("alt", "Generated image")})


def _sanitize_text(value: str) -> str:
    """前後空白除去・制御文字除去・空文字拒否の共通バリデーション"""
    value = value.strip()
    value = _CONTROL_CHAR_RE.sub("", value)
    if not value:
        raise ValueError("メッセージが空です")
    return value


def _sanitize_artifact_payload(value: str) -> str:
    """安全性判定前に data URI を縮約する。"""
    return _DATA_URI_RE.sub("[data-uri]", value)


def _truncate_for_safety(value: str, limit: int = 9000) -> str:
    """Content Safety の入力上限に収まるよう文字数を制限する。"""
    return value if len(value) <= limit else value[:limit]


def _extract_message_text(message: object) -> str:
    """Agent Framework の Message からテキストを抽出する。"""
    contents = getattr(message, "contents", None)
    if not contents:
        return ""

    text_parts: list[str] = []
    for content in contents:
        text = getattr(content, "text", None)
        if isinstance(text, str) and text.strip():
            text_parts.append(text)
    return "".join(text_parts).strip()


def _extract_result_text(result: object) -> str:
    """agent.run() の返り値からアシスタント本文を取り出す。"""
    if result is None:
        return ""

    direct_text = _extract_message_text(result)
    if direct_text:
        return direct_text

    try:
        outputs = result.get_outputs()
    except (AttributeError, TypeError) as exc:
        logger.debug("get_outputs() 失敗: %s", exc)
        outputs = []
    except (RuntimeError, OSError) as exc:
        logger.debug("get_outputs() で予期しないエラー: %s", exc)
        outputs = []

    messages: list[object] = []
    for output in outputs:
        if isinstance(output, list):
            messages.extend(output)
            continue
        messages.append(output)

    for message in reversed(messages):
        message_text = _extract_message_text(message)
        if message_text:
            return message_text

    return str(result).strip()


def _extract_total_tokens(result: object) -> int:
    """agent.run() の返り値からトークン使用量を取り出す。"""
    if result is None:
        return 0
    # Responses API は usage 属性を返す
    usage = getattr(result, "usage", None)
    if usage is not None:
        return getattr(usage, "total_tokens", 0) or 0
    # get_outputs() 経由で最後の output の usage を探す
    try:
        for output in result.get_outputs() if hasattr(result, "get_outputs") else []:
            out_usage = getattr(output, "usage", None)
            if out_usage:
                return getattr(out_usage, "total_tokens", 0) or 0
    except AttributeError, TypeError, RuntimeError:
        pass
    return 0


def _extract_brochure_html(result_text: str) -> str | None:
    """Agent4 の返答から HTML ブローシャ本体を抽出する。"""
    match = _BROCHURE_HTML_BLOCK_RE.search(result_text)
    if match:
        return match.group(1).strip()

    lowered = result_text.lower()
    html_start = lowered.find("<!doctype html")
    if html_start == -1:
        html_start = lowered.find("<html")
    if html_start == -1:
        return None
    return result_text[html_start:].strip()


def _extract_inline_images(html_content: str) -> list[dict[str, str]]:
    """HTML 内に埋め込まれた data URI 画像を抽出する。"""
    parser = _InlineImageExtractor()
    parser.feed(html_content)
    parser.close()
    return parser.images


def _inject_images_into_html(html: str, images: dict[str, str]) -> str:
    """生成画像をブローシャ HTML に埋め込む。"""
    hero = images.get("hero", "")

    # HERO_IMAGE プレースホルダーを置換
    if hero and "HERO_IMAGE" in html:
        html = html.replace("HERO_IMAGE", hero)

    # プレースホルダーがない場合、最初の <main> または <body> タグの直後に挿入
    if hero and "HERO_IMAGE" not in html and hero not in html:
        img_tag = f'<img src="{hero}" alt="メインビジュアル" class="w-full rounded-lg mb-6" />'
        for insert_point in ["<main", "<body"]:
            idx = html.lower().find(insert_point)
            if idx >= 0:
                close = html.find(">", idx)
                if close >= 0:
                    html = html[: close + 1] + "\n" + img_tag + "\n" + html[close + 1 :]
                    break

    # バナー画像も埋め込む（あれば）
    for key, uri in images.items():
        if key.startswith("banner_") and uri and uri not in html:
            footer_idx = html.lower().find("<footer")
            if footer_idx >= 0:
                banner_tag = f'<img src="{uri}" alt="SNSバナー ({key})" class="w-full rounded-lg my-4" />'
                html = html[:footer_idx] + banner_tag + "\n" + html[footer_idx:]
            else:
                # footer がなければ </body> の前に挿入
                body_end = html.lower().find("</body")
                if body_end >= 0:
                    banner_tag = f'<img src="{uri}" alt="SNSバナー ({key})" class="w-full rounded-lg my-4" />'
                    html = html[:body_end] + banner_tag + "\n" + html[body_end:]

    return html


def _extract_code_interpreter_images(result: object) -> list[dict[str, str]]:
    """Code Interpreter の出力から画像データを抽出する。

    Responses API の code_interpreter_call 出力アイテムを走査し、
    画像出力（base64 data URI または file_id）を収集する。
    Code Interpreter が画像を生成しなかった場合は空リストを返す。
    """
    images: list[dict[str, str]] = []
    try:
        outputs = result.get_outputs() if hasattr(result, "get_outputs") else []
    except (AttributeError, TypeError, RuntimeError, OSError) as exc:
        logger.debug("Code Interpreter 画像抽出で get_outputs() 失敗: %s", exc)
        return images

    all_items: list[object] = []
    for output in outputs:
        if isinstance(output, list):
            all_items.extend(output)
        else:
            all_items.append(output)

    for item in all_items:
        item_type = getattr(item, "type", "")
        if item_type != "code_interpreter_call":
            continue
        # code_interpreter_call の outputs を走査
        ci_result = getattr(item, "code_interpreter", None) or item
        ci_outputs = getattr(ci_result, "outputs", []) or []
        for ci_out in ci_outputs:
            out_type = getattr(ci_out, "type", "")
            if out_type == "image":
                image_obj = getattr(ci_out, "image", None)
                if image_obj is None:
                    continue
                # inline base64 データがある場合
                b64_data = getattr(image_obj, "data", "") or getattr(image_obj, "b64_json", "")
                if b64_data:
                    images.append(
                        {
                            "url": f"data:image/png;base64,{b64_data}",
                            "alt": "データ分析グラフ（Code Interpreter）",
                        }
                    )
                    continue
                # file_id 参照の場合（ログのみ。ダウンロードは別途対応）
                file_id = getattr(image_obj, "file_id", "")
                if file_id:
                    logger.info("Code Interpreter 画像ファイル検出: file_id=%s", file_id)

    return images


def _build_content_events(agent_name: str, result_text: str) -> list[str]:
    """エージェント出力を UI 用の SSE イベントに変換する。"""
    if agent_name == "brochure-gen-agent":
        html_content = _extract_brochure_html(result_text)
        if html_content:
            events = [
                format_sse(
                    SSEEventType.TEXT,
                    {"content": html_content, "agent": agent_name, "content_type": "html"},
                )
            ]
            for image in _extract_inline_images(html_content):
                events.append(
                    format_sse(
                        SSEEventType.IMAGE,
                        {"url": image["url"], "alt": image["alt"], "agent": agent_name},
                    )
                )
            return events

    if not result_text:
        return []

    return [format_sse(SSEEventType.TEXT, {"content": result_text, "agent": agent_name})]


def _build_marketing_plan_prompt(user_input: str, analysis_markdown: str) -> str:
    """Agent2 に渡す企画書生成プロンプトを組み立てる。"""
    return (
        "以下の依頼と分析結果をもとに、旅行マーケティング企画書を作成してください。\n\n"
        f"## ユーザー依頼\n{user_input}\n\n"
        f"## Agent1 の分析結果\n{analysis_markdown}\n"
    )


def _build_revision_prompt(context: PendingApprovalContext, revision_text: str) -> str:
    """承認前の修正指示を Agent2 に渡すプロンプトを組み立てる。"""
    return (
        "以下の旅行企画書を、修正指示だけ反映して再作成してください。\n\n"
        f"## 元の依頼\n{context['user_input']}\n\n"
        f"## Agent1 の分析結果\n{context['analysis_markdown']}\n\n"
        f"## 現在の企画書\n{context['plan_markdown']}\n\n"
        f"## 修正指示\n{revision_text}\n"
    )


def _extract_plan_title(plan_markdown: str) -> str:
    """企画書 Markdown からタイトルを抽出する。"""
    for line in plan_markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("# ").strip() or "旅行マーケティング企画書"
    return "旅行マーケティング企画書"


async def _build_brochure_fallback_outcome(
    events: list[str],
    source_text: str,
    conversation_id: str,
    step: int,
    total_steps: int,
    include_done: bool,
    start_time: float,
) -> AgentExecutionOutcome:
    """Agent4 が失敗したときに最低限の販促物を返す。"""
    from src.agents.brochure_gen import generate_banner_image, generate_hero_image

    title = _extract_plan_title(source_text)
    hero_image = await generate_hero_image(
        prompt="Bright family travel campaign hero image with resort atmosphere",
        destination=title,
        style="photorealistic",
    )
    banner_image = await generate_banner_image(
        prompt=f"Travel promotion banner for {title}",
        platform="instagram",
    )
    escaped_source = escape(source_text).replace("\n", "<br />")
    html_content = f"""<!DOCTYPE html>
<html lang=\"ja\">
<head>
  <meta charset=\"UTF-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
  <title>{escape(title)}</title>
</head>
<body style=\"margin:0;font-family:'Noto Sans JP',system-ui,sans-serif;background:#f8fafc;color:#0f172a;\">
  <header style=\"padding:48px 24px;background:linear-gradient(135deg,#0f766e,#0ea5e9);color:white;\">
    <div style=\"max-width:960px;margin:0 auto;\">
      <p style=\"margin:0 0 12px;font-size:14px;letter-spacing:0.12em;text-transform:uppercase;opacity:0.82;\">Travel Marketing AI</p>
      <h1 style=\"margin:0 0 18px;font-size:40px;line-height:1.2;\">{escape(title)}</h1>
      <p style=\"margin:0;font-size:16px;line-height:1.8;max-width:720px;\">AI 生成が混雑したため、販促物をフォールバック生成で仕上げています。内容は最新の規制チェック済み企画書を反映しています。</p>
    </div>
  </header>
  <main style=\"max-width:960px;margin:0 auto;padding:32px 24px 48px;\">
    <section style=\"overflow:hidden;border:1px solid #dbeafe;border-radius:24px;background:white;box-shadow:0 18px 48px rgba(15,23,42,0.08);\">
      <img src=\"{hero_image}\" alt=\"{escape(title)} ヒーロー画像\" style=\"display:block;width:100%;height:auto;background:#e2e8f0;\" />
      <div style=\"padding:24px;\">
        <h2 style=\"margin:0 0 12px;font-size:24px;\">企画書サマリ</h2>
        <div style=\"font-size:15px;line-height:1.9;color:#334155;word-break:break-word;\">{escaped_source}</div>
      </div>
    </section>
    <section style=\"margin-top:24px;overflow:hidden;border:1px solid #e2e8f0;border-radius:24px;background:white;\">
      <img src=\"{banner_image}\" alt=\"{escape(title)} SNS バナー\" style=\"display:block;width:100%;height:auto;background:#e2e8f0;\" />
    </section>
  </main>
  <footer style=\"padding:20px 24px;background:#0f172a;color:#cbd5e1;font-size:12px;line-height:1.8;\">
    旅行会社登録番号: 観光庁長官登録旅行業第0000号<br />
    詳細な取引条件は正式な募集要項をご確認ください。
  </footer>
</body>
</html>"""

    for tool_name in _TOOL_EVENT_HINTS.get("brochure-gen-agent", []):
        events.append(
            format_sse(
                SSEEventType.TOOL_EVENT,
                {"tool": tool_name, "status": "completed", "agent": "brochure-gen-agent"},
            )
        )
    events.append(
        format_sse(
            SSEEventType.TEXT,
            {"content": html_content, "agent": "brochure-gen-agent", "content_type": "html"},
        )
    )
    events.append(
        format_sse(
            SSEEventType.IMAGE,
            {"url": hero_image, "alt": f"{title} ヒーロー画像", "agent": "brochure-gen-agent"},
        )
    )
    events.append(
        format_sse(
            SSEEventType.IMAGE,
            {"url": banner_image, "alt": f"{title} SNS バナー", "agent": "brochure-gen-agent"},
        )
    )
    events.append(
        format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": "brochure-gen-agent", "status": "completed", "step": step, "total_steps": total_steps},
        )
    )

    safety_input = _truncate_for_safety(_sanitize_artifact_payload(html_content))
    safety_scores = await analyze_content(safety_input)
    events.append(
        format_sse(
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
                        value == 0
                        for value in [
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
    )

    elapsed = round(time.monotonic() - start_time, 1)
    if include_done:
        events.append(
            format_sse(
                SSEEventType.DONE,
                {
                    "conversation_id": conversation_id,
                    "metrics": {"latency_seconds": elapsed, "tool_calls": 2, "total_tokens": 0},
                },
            )
        )

    return {
        "events": events,
        "text": html_content,
        "success": True,
        "latency_seconds": elapsed,
        "tool_calls": 2,
    }


def _conversation_status_from_events(events: list[dict]) -> str:
    """保存対象イベント列から会話ステータスを推定する。"""
    if not events:
        return "completed"

    last_event = events[-1].get("event")
    if last_event in {SSEEventType.DONE, SSEEventType.DONE.value}:
        return "completed"
    if last_event in {SSEEventType.APPROVAL_REQUEST, SSEEventType.APPROVAL_REQUEST.value}:
        return "awaiting_approval"
    if last_event in {SSEEventType.ERROR, SSEEventType.ERROR.value}:
        return "error"
    return "completed"


async def _load_pending_approval_context(conversation_id: str) -> PendingApprovalContext | None:
    """承認待ちコンテキストをメモリまたは保存済み会話から復元する。"""
    context = _pending_approvals.get(conversation_id)
    if context:
        return context

    conversation = await get_conversation(conversation_id)
    if not conversation:
        return None
    if conversation.get("status") != "awaiting_approval":
        return None

    analysis_markdown = ""
    plan_markdown = ""
    for message in conversation.get("messages", []):
        event_name = message.get("event")
        data = message.get("data", {})
        if event_name == SSEEventType.APPROVAL_REQUEST.value:
            plan_markdown = data.get("plan_markdown", plan_markdown)
        if event_name != SSEEventType.TEXT.value:
            continue
        agent_name = data.get("agent")
        if agent_name == "data-search-agent" and not analysis_markdown:
            analysis_markdown = data.get("content", "")
        if agent_name == "marketing-plan-agent":
            plan_markdown = data.get("content", plan_markdown)

    if not plan_markdown:
        return None

    context = {
        "user_input": conversation.get("input", ""),
        "analysis_markdown": analysis_markdown,
        "plan_markdown": plan_markdown,
        "model_settings": None,
    }
    _pending_approvals[conversation_id] = context
    return context


def _is_retryable_agent_error(exc: Exception) -> bool:
    """一時的な失敗かどうかを判定する。"""
    message = str(exc).lower()
    # コンテキスト長超過やバリデーションエラーはリトライしても無駄
    if any(kw in message for kw in ["context_length_exceeded", "invalid_payload", "invalid_request_error"]):
        return False
    return any(
        keyword in message
        for keyword in [
            "429",
            "rate limit",
            "too many requests",
            "timeout",
            "temporarily",
            "500",
            "server_error",
            "502",
            "503",
            "504",
        ]
    )


def _is_code_interpreter_404(exc: Exception) -> bool:
    """Code Interpreter の 404 エラーかを判定する。

    Responses API が Code Interpreter コンテナを見つけられない場合に
    返す 404 エラーを検出する。
    """
    message = str(exc).lower()
    return "404" in message and "resource not found" in message


async def _execute_agent(
    agent_name: str,
    agent_step: int,
    user_input: str,
    conversation_id: str,
    model_settings: dict | None = None,
    total_steps: int = _PIPELINE_TOTAL_STEPS,
    include_done: bool = False,
) -> AgentExecutionOutcome:
    """単一エージェントを実行し、SSE イベント列と結果テキストを返す。"""
    from src.agents import (
        create_brochure_gen_agent,
        create_data_search_agent,
        create_marketing_plan_agent,
        create_plan_revision_agent,
        create_regulation_check_agent,
        create_video_gen_agent,
    )

    # OpenTelemetry スパン（計測用）
    span = None
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("travel-marketing-agents")
        span = tracer.start_span(
            f"agent.{agent_name}",
            attributes={
                "agent.name": agent_name,
                "agent.step": agent_step,
                "conversation.id": conversation_id,
            },
        )
    except ImportError, RuntimeError:
        pass

    # brochure-gen-agent の場合、side-channel の conversation_id を設定
    if agent_name == "brochure-gen-agent":
        from src.agents.brochure_gen import set_current_conversation_id

        set_current_conversation_id(conversation_id)

    start_time = time.monotonic()
    agent_map = {
        "data-search-agent": (create_data_search_agent, 1),
        "marketing-plan-agent": (create_marketing_plan_agent, 2),
        "regulation-check-agent": (create_regulation_check_agent, 4),
        "plan-revision-agent": (create_plan_revision_agent, 4),
        "brochure-gen-agent": (create_brochure_gen_agent, 5),
        "video-gen-agent": (create_video_gen_agent, 5),
    }
    create_fn, default_step = agent_map.get(agent_name, (create_marketing_plan_agent, 2))
    step = agent_step or default_step
    events = [
        format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": agent_name, "status": "running", "step": step, "total_steps": total_steps},
        )
    ]

    result = None
    delay_seconds = 5.0
    max_attempts = 5
    for attempt in range(1, max_attempts + 1):
        try:
            agent = create_fn(model_settings)
            result = await agent.run(user_input)
            break
        except Exception as exc:
            # Code Interpreter 404: 無効化してリトライ（リトライ回数を消費しない）
            if agent_name == "data-search-agent" and _is_code_interpreter_404(exc):
                from src.agents.data_search import _should_enable_code_interpreter, set_code_interpreter_available

                if _should_enable_code_interpreter():
                    set_code_interpreter_available(False)
                    logger.warning("Code Interpreter 404 を検出。無効化してリトライします: %s", exc)
                    continue

            if agent_name == "brochure-gen-agent" and attempt == max_attempts:
                logger.warning("brochure-gen-agent の通常生成に失敗したためフォールバックを返します: %s", exc)
                return await _build_brochure_fallback_outcome(
                    events=events,
                    source_text=user_input,
                    conversation_id=conversation_id,
                    step=step,
                    total_steps=total_steps,
                    include_done=include_done,
                    start_time=start_time,
                )

            if attempt == max_attempts or not _is_retryable_agent_error(exc):
                logger.exception("エージェント(%s)の実行に失敗", agent_name)
                # クライアントには内部詳細を返さない
                error_msg = f"{agent_name} の実行に失敗しました。しばらく待ってから再試行してください。"
                if "429" in str(exc) or "too many requests" in str(exc).lower():
                    error_msg = "API のレート制限に達しました。30 秒ほど待ってから再試行してください。"
                events.append(
                    format_sse(
                        SSEEventType.ERROR,
                        {"message": error_msg, "code": "AGENT_RUNTIME_ERROR"},
                    )
                )
                return {
                    "events": events,
                    "text": "",
                    "success": False,
                    "latency_seconds": round(time.monotonic() - start_time, 1),
                    "tool_calls": 0,
                }

            logger.warning(
                "エージェント(%s)で一時エラーが発生。%d 回目を %.1f 秒後に再試行します: %s",
                agent_name,
                attempt + 1,
                delay_seconds,
                exc,
            )
            await asyncio.sleep(delay_seconds)
            delay_seconds = delay_seconds * 2 + random.uniform(0, delay_seconds * 0.3)

    result_text = _extract_result_text(result)
    total_tokens = _extract_total_tokens(result)
    safety_input = _truncate_for_safety(_sanitize_artifact_payload(result_text))

    tool_shield = await check_tool_response(safety_input)
    if not tool_shield.is_safe:
        events.append(
            format_sse(
                SSEEventType.SAFETY,
                {"status": "blocked", "reason": "tool_response_unsafe", "details": tool_shield.details},
            )
        )
        events.append(
            format_sse(
                SSEEventType.ERROR,
                {"message": "ツール応答が安全性チェックに失敗しました", "code": "TOOL_RESPONSE_BLOCKED"},
            )
        )
        return {
            "events": events,
            "text": "",
            "success": False,
            "latency_seconds": round(time.monotonic() - start_time, 1),
            "tool_calls": 0,
        }

    for tool_name in _TOOL_EVENT_HINTS.get(agent_name, []):
        events.append(
            format_sse(
                SSEEventType.TOOL_EVENT,
                {"tool": tool_name, "status": "completed", "agent": agent_name},
            )
        )

    events.extend(_build_content_events(agent_name, result_text))

    # Side-channel 画像の取得（brochure-gen-agent のツールが画像を side-channel に保存する）
    if agent_name == "brochure-gen-agent":
        from src.agents.brochure_gen import pop_pending_images, set_current_conversation_id

        set_current_conversation_id(conversation_id)
        pending = pop_pending_images(conversation_id)

        # ブローシャ HTML に画像を埋め込む
        if pending:
            for i, evt in enumerate(events):
                if '"content_type": "html"' in evt and '"brochure-gen-agent"' in evt:
                    data_match = re.search(r"data: ({.*})", evt)
                    if data_match:
                        try:
                            data = json.loads(data_match.group(1))
                            html_content = data.get("content", "")
                            if "<html" in html_content.lower() or "<!doctype" in html_content.lower():
                                data["content"] = _inject_images_into_html(html_content, pending)
                                events[i] = (
                                    f"event: {SSEEventType.TEXT.value}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                                )
                        except json.JSONDecodeError, AttributeError:
                            pass
                    break

        # IMAGE イベントも別途送出（Images タブ用）
        for img_key, img_data_uri in pending.items():
            events.append(
                format_sse(
                    SSEEventType.IMAGE,
                    {"url": img_data_uri, "alt": f"Generated {img_key} image", "agent": agent_name},
                )
            )

        # 注: 動画ジョブの pop + polling は _post_approval_events() で一括処理する。
        # ここで pop すると二重消費になるため、通知メッセージのみ出す。

    # Code Interpreter 画像の取得（data-search-agent がグラフを生成する場合）
    if agent_name == "data-search-agent" and result is not None:
        ci_images = _extract_code_interpreter_images(result)
        for ci_img in ci_images:
            events.append(
                format_sse(
                    SSEEventType.IMAGE,
                    {"url": ci_img["url"], "alt": ci_img["alt"], "agent": agent_name},
                )
            )

    events.append(
        format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": agent_name, "status": "completed", "step": step, "total_steps": total_steps},
        )
    )

    if safety_input.strip():
        safety_scores = await analyze_content(safety_input)
        safety_payload = {
            "hate": safety_scores.hate,
            "self_harm": safety_scores.self_harm,
            "sexual": safety_scores.sexual,
            "violence": safety_scores.violence,
            "status": "error"
            if safety_scores.check_failed
            else (
                "safe"
                if all(
                    value == 0
                    for value in [
                        safety_scores.hate,
                        safety_scores.self_harm,
                        safety_scores.sexual,
                        safety_scores.violence,
                    ]
                )
                else "warning"
            ),
        }
    else:
        safety_payload = {"hate": 0, "self_harm": 0, "sexual": 0, "violence": 0, "status": "safe"}

    events.append(format_sse(SSEEventType.SAFETY, safety_payload))

    elapsed = round(time.monotonic() - start_time, 1)
    tool_calls = len(_TOOL_EVENT_HINTS.get(agent_name, []))

    # OpenTelemetry スパンに結果を記録
    if span:
        try:
            span.set_attribute("agent.latency_seconds", elapsed)
            span.set_attribute("agent.total_tokens", total_tokens)
            span.set_attribute("agent.tool_calls", tool_calls)
            span.set_attribute("agent.success", True)
            span.end()
        except RuntimeError, ValueError:
            pass

    if include_done:
        events.append(
            format_sse(
                SSEEventType.DONE,
                {
                    "conversation_id": conversation_id,
                    "metrics": {
                        "latency_seconds": elapsed,
                        "tool_calls": tool_calls,
                        "total_tokens": total_tokens,
                    },
                },
            )
        )

    return {
        "events": events,
        "text": result_text,
        "success": True,
        "latency_seconds": elapsed,
        "tool_calls": tool_calls,
        "total_tokens": total_tokens,
    }


async def _maybe_run_quality_review(review_input: str) -> list[str]:
    """Agent5 の品質レビューをオプショナルに実行する。"""
    if not review_input.strip():
        return []

    try:
        from src.agents import create_review_agent

        review_agent = create_review_agent()
        if review_agent is None:
            return []

        review_result = await review_agent.run(review_input)
        review_text = _extract_result_text(review_result)
        if not review_text:
            return []

        return [format_sse(SSEEventType.TEXT, {"content": review_text, "agent": "quality-review-agent"})]
    except (ImportError, ValueError, OSError) as exc:
        logger.warning("Agent5 品質レビューの実行に失敗（スキップ）: %s", exc)
        return []
    except RuntimeError, TypeError:
        logger.warning("Agent5 品質レビューの実行に失敗（スキップ）", exc_info=True)
        return []


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

    # 販促動画生成（モック）
    yield format_sse(
        SSEEventType.TOOL_EVENT,
        {
            "tool": "generate_promo_video",
            "status": "completed",
            "agent": "video-gen-agent",
        },
    )
    await asyncio.sleep(0.2)

    yield format_sse(
        SSEEventType.TEXT,
        {
            "agent": "video-gen-agent",
            "content": "🎬 販促動画を生成中です（ジョブID: promo-mock-12345）。完了まで数分かかります。",
            "content_type": "text",
        },
    )
    await asyncio.sleep(0.2)

    # Mock 販促動画 URL
    yield format_sse(
        SSEEventType.TEXT,
        {
            "content": "https://example.com/mock-promo-video.mp4",
            "agent": "video-gen-agent",
            "content_type": "video",
        },
    )
    await asyncio.sleep(0.2)

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


async def _run_single_agent(
    agent_name: str,
    agent_step: int,
    user_input: str,
    conversation_id: str,
    model_settings: dict | None = None,
):
    """個別エージェントを実行して SSE イベントを生成する。"""
    outcome = await _execute_agent(
        agent_name=agent_name,
        agent_step=agent_step,
        user_input=user_input,
        conversation_id=conversation_id,
        model_settings=model_settings,
        include_done=True,
    )
    for event in outcome["events"]:
        yield event


async def _refine_events(refine_text: str, conversation_id: str):
    """完了後のマルチターン修正リクエストを処理する SSE イベント"""
    pending_context = await _load_pending_approval_context(conversation_id)
    if pending_context is not None:
        outcome = await _execute_agent(
            agent_name="marketing-plan-agent",
            agent_step=2,
            user_input=_build_revision_prompt(pending_context, refine_text),
            conversation_id=conversation_id,
            model_settings=pending_context.get("model_settings"),
        )
        for event in outcome["events"]:
            yield event
        if not outcome["success"]:
            return

        _pending_approvals[conversation_id] = {
            **pending_context,
            "plan_markdown": outcome["text"],
        }
        yield format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": "approval", "status": "running", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
        )
        yield format_sse(
            SSEEventType.APPROVAL_REQUEST,
            {
                "prompt": "修正した企画書を確認してください。承認する場合は「承認」、さらに修正したい場合は修正内容を入力してください。",
                "conversation_id": conversation_id,
                "plan_markdown": outcome["text"],
            },
        )
        return

    text_lower = refine_text.lower()
    if any(k in text_lower for k in ["画像", "バナー", "イメージ", "色", "明るく"]):
        target_agent = "brochure-gen-agent"
        step = 5
    elif any(k in text_lower for k in ["規制", "法令", "チェック", "表現"]):
        target_agent = "regulation-check-agent"
        step = 4
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


def _get_reference_brochure_path() -> str | None:
    """既存パンフレットPDFのパスを取得する。"""
    settings = get_settings()
    if not settings.get("content_understanding_endpoint"):
        return None
    # data/ ディレクトリ内の PDF ファイルを検索（最新アップロード優先）
    data_dir = Path(__file__).resolve().parent.parent.parent / "data"
    pdf_files = sorted(data_dir.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
    if pdf_files:
        return str(pdf_files[0])
    return None


def _extract_plan_summary(plan_text: str) -> str:
    """企画書から100〜200文字のサマリを抽出する。"""
    lines = plan_text.strip().split("\n")
    summary_parts = []
    for line in lines[:20]:
        line = line.strip().lstrip("#").strip()
        if line and not line.startswith("|") and not line.startswith("-"):
            summary_parts.append(line)
        if len("".join(summary_parts)) > 200:
            break
    return "".join(summary_parts)[:200] if summary_parts else plan_text[:200]


async def _post_approval_events(user_response: str, conversation_id: str):
    """承認後に Agent3 → Agent4 を実行する SSE イベント"""
    settings = get_settings()

    if not settings["project_endpoint"]:
        async for event in _mock_post_approval_events(conversation_id):
            yield event
        return

    context = await _load_pending_approval_context(conversation_id)
    if context is None:
        yield format_sse(
            SSEEventType.ERROR,
            {
                "message": "承認対象の企画書が見つかりません。最初から再実行してください。",
                "code": "APPROVAL_CONTEXT_NOT_FOUND",
            },
        )
        return

    total_tool_calls = 0
    total_tokens_sum = 0
    approval_start = time.monotonic()

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "approval", "status": "completed", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
    )

    # 承認完了後、すぐに規制チェックの開始を通知（タイマーリセット用）
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "regulation-check-agent", "status": "running", "step": 4, "total_steps": _PIPELINE_TOTAL_STEPS},
    )

    regulation_input = context["plan_markdown"]

    # 🚀 並列化: 規制チェック中にヒーロー画像を先行生成（Agent4 の画像生成を高速化）
    hero_image_task = None
    destination_text = None
    # 旅行先を事前抽出（画像の先行生成 + Agent4 入力に使用）
    dest_match = re.search(r"(?:旅行先|目的地|プラン名)[：:]\s*(.+?)[\n。]", regulation_input)
    if dest_match:
        destination_text = dest_match.group(1).strip()
    else:
        for place in ["北海道", "沖縄", "京都", "東京", "九州", "東北", "四国", "北陸"]:
            if place in regulation_input:
                destination_text = place
                break

    if destination_text and settings["project_endpoint"]:
        from src.agents.brochure_gen import set_current_conversation_id

        set_current_conversation_id(conversation_id)

        async def _pregenerate_hero():
            from src.agents.brochure_gen import generate_hero_image

            try:
                await generate_hero_image(
                    prompt=f"Beautiful travel destination scenery of {destination_text}",
                    destination=destination_text,
                    style="photorealistic",
                )
                logger.info("ヒーロー画像の先行生成完了: %s", destination_text)
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning("ヒーロー画像の先行生成に失敗（Agent4 で再生成）: %s", exc)

        hero_image_task = asyncio.create_task(_pregenerate_hero())

    regulation_outcome = await _execute_agent(
        agent_name="regulation-check-agent",
        agent_step=4,
        user_input=regulation_input,
        conversation_id=conversation_id,
        model_settings=context.get("model_settings"),
    )
    for event in regulation_outcome["events"]:
        yield event
    if not regulation_outcome["success"]:
        return
    total_tool_calls += regulation_outcome["tool_calls"]
    total_tokens_sum += regulation_outcome.get("total_tokens", 0)

    # Agent3b: 規制チェック結果を反映した修正版企画書を生成
    revision_input = (
        f"## 元の企画書\n\n{context['plan_markdown']}\n\n"
        f"## 規制チェック結果\n\n{regulation_outcome['text']}"
    )
    revision_outcome = await _execute_agent(
        agent_name="plan-revision-agent",
        agent_step=4,
        user_input=revision_input,
        conversation_id=conversation_id,
        model_settings=context.get("model_settings"),
    )
    for event in revision_outcome["events"]:
        yield event
    if not revision_outcome["success"]:
        return
    total_tool_calls += revision_outcome["tool_calls"]
    total_tokens_sum += revision_outcome.get("total_tokens", 0)

    # Agent4 への入力は修正版企画書
    brochure_input = revision_outcome["text"]

    # 旅行先を入力に明示（画像生成の精度向上）— 先行生成で抽出済みの destination_text を再利用
    if destination_text:
        brochure_input = f"[旅行先: {destination_text}]\n\n{brochure_input}"

    # 既存パンフレット参照（Content Understanding）
    reference_pdf = _get_reference_brochure_path()
    if reference_pdf:
        brochure_input = f"[参考パンフレット: {reference_pdf}]\n\n{brochure_input}"

    # 先行生成タスクの完了を待機（Agent4 開始前に画像が side-channel に入っている必要がある）
    if hero_image_task:
        await hero_image_task

    brochure_outcome = await _execute_agent(
        agent_name="brochure-gen-agent",
        agent_step=5,
        user_input=brochure_input,
        conversation_id=conversation_id,
        model_settings=context.get("model_settings"),
    )
    for event in brochure_outcome["events"]:
        yield event
    if not brochure_outcome["success"]:
        return
    total_tool_calls += brochure_outcome["tool_calls"]
    total_tokens_sum += brochure_outcome.get("total_tokens", 0)

    # Agent5: 販促動画生成（Photo Avatar）
    # 企画書サマリを渡して動画を生成
    video_summary = _extract_plan_summary(brochure_input)
    if video_summary:
        video_outcome = await _execute_agent(
            agent_name="video-gen-agent",
            agent_step=5,
            user_input=video_summary,
            conversation_id=conversation_id,
            model_settings=context.get("model_settings"),
        )
        for event in video_outcome["events"]:
            yield event
        total_tool_calls += video_outcome["tool_calls"]
        total_tokens_sum += video_outcome.get("total_tokens", 0)

    # Video polling
    from src.agents.video_gen import poll_video_job, pop_pending_video_job

    video_job = pop_pending_video_job()
    if video_job and video_job.get("job_id"):
        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": "🎬 販促動画を生成中です...",
                "agent": "video-gen-agent",
                "content_type": "text",
            },
        )
        video_url = await poll_video_job(video_job["job_id"], max_wait=120)
        if video_url and video_url.startswith("https://"):
            yield format_sse(
                SSEEventType.TEXT,
                {
                    "content": video_url,
                    "agent": "video-gen-agent",
                    "content_type": "video",
                },
            )
        elif video_url:
            logger.warning("Photo Avatar: 無効な video_url を無視: %s", video_url[:100])

    review_input = "\n\n".join(
        part
        for part in [
            context["analysis_markdown"],
            context["plan_markdown"],
            regulation_outcome["text"],
            brochure_outcome["text"],
        ]
        if part
    )
    for event in await _maybe_run_quality_review(review_input):
        yield event

    await _trigger_logic_app(
        conversation_id=conversation_id,
        plan_markdown=context["plan_markdown"],
        brochure_html=_extract_brochure_html(brochure_outcome["text"]) or "",
    )
    _pending_approvals.pop(conversation_id, None)

    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "metrics": {
                "latency_seconds": round(time.monotonic() - approval_start, 1),
                "tool_calls": total_tool_calls,
                "total_tokens": total_tokens_sum,
            },
        },
    )


async def _trigger_logic_app(conversation_id: str, plan_markdown: str, brochure_html: str) -> None:
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
            "plan_title": _extract_plan_title(plan_markdown),
            "plan_markdown": plan_markdown,
            "brochure_html": brochure_html,
            "conversation_id": conversation_id,
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
    except (ValueError, OSError) as exc:
        logger.warning("Logic Apps コールバック送信に失敗（非致命的）: %s", exc)
    except RuntimeError, urllib.error.URLError:
        logger.warning("Logic Apps コールバック送信に失敗（非致命的）", exc_info=True)


# --- エンドポイント ---


async def workflow_event_generator(user_input: str, conversation_id: str, model_settings: dict | None = None):
    """実際の Workflow を実行して SSE イベントを生成する（Azure 接続時）"""
    analysis_outcome = await _execute_agent(
        agent_name="data-search-agent",
        agent_step=1,
        user_input=user_input,
        conversation_id=conversation_id,
        model_settings=model_settings,
    )
    for event in analysis_outcome["events"]:
        yield event
    if not analysis_outcome["success"]:
        return

    plan_outcome = await _execute_agent(
        agent_name="marketing-plan-agent",
        agent_step=2,
        user_input=_build_marketing_plan_prompt(user_input, analysis_outcome["text"]),
        conversation_id=conversation_id,
        model_settings=model_settings,
    )
    for event in plan_outcome["events"]:
        yield event
    if not plan_outcome["success"]:
        return

    _pending_approvals[conversation_id] = {
        "user_input": user_input,
        "analysis_markdown": analysis_outcome["text"],
        "plan_markdown": plan_outcome["text"],
        "model_settings": model_settings,
    }

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "approval", "status": "running", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
    )
    yield format_sse(
        SSEEventType.APPROVAL_REQUEST,
        {
            "prompt": "上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
            "conversation_id": conversation_id,
            "plan_markdown": plan_outcome["text"],
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
                async for event in _collect_and_yield(
                    workflow_event_generator(body.message, conversation_id, body.settings)
                ):
                    yield event
            else:
                async for event in _collect_and_yield(mock_event_generator(body.message, conversation_id)):
                    yield event

        # 会話を非同期で保存（レスポンスには影響しない）
        try:
            conversation_status = _conversation_status_from_events(collected_events)
            await save_conversation(
                conversation_id,
                body.message,
                collected_events,
                status=conversation_status,
            )
        except (ValueError, OSError) as exc:
            logger.debug("会話保存に失敗（非致命的）: %s", exc)
        except Exception as exc:
            logger.debug("会話保存で予期しないエラー（非致命的）: %s", exc)

        # Agent5: 品質レビュー（バックグラウンドで実行、オプショナル）
        settings = get_settings()
        if settings["project_endpoint"] and not body.conversation_id and conversation_status == "completed":
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
            except (ImportError, ValueError, OSError) as exc:
                logger.warning("Agent5 品質レビューの実行に失敗（スキップ）: %s", exc)
            except RuntimeError, TypeError:
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
        collected_events: list[dict] = []
        start = time.monotonic()

        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {"message": "入力が安全性チェックに失敗しました", "code": "PROMPT_SHIELD_BLOCKED"},
            )
            return

        async def _collect_and_yield(gen):
            async for event in gen:
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

        existing_conversation = await get_conversation(thread_id)
        if is_approved:
            async for event in _collect_and_yield(_post_approval_events(body.response, thread_id)):
                yield event
        else:
            async for event in _collect_and_yield(_refine_events(body.response, thread_id)):
                yield event

        try:
            previous_messages = existing_conversation.get("messages", []) if existing_conversation else []
            merged_messages = [*previous_messages, *collected_events]
            await save_conversation(
                conversation_id=thread_id,
                user_input=existing_conversation.get("input", body.response)
                if existing_conversation
                else body.response,
                events=merged_messages,
                status=_conversation_status_from_events(merged_messages),
            )
        except (ValueError, OSError) as exc:
            logger.debug("承認系会話の保存に失敗（非致命的）: %s", exc)
        except RuntimeError, TypeError:
            logger.debug("承認系会話の保存に失敗（非致命的）", exc_info=True)

    return StreamingResponse(
        approval_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


_UPLOAD_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_ALLOWED_DIR = Path(__file__).resolve().parent.parent.parent / "data"


@router.post("/upload-pdf")
@limiter.limit("5/minute")
async def upload_pdf(request: Request, file: UploadFile) -> JSONResponse:
    """既存パンフレット PDF をアップロードし、data/ に保存する。

    Content Understanding (Agent4) が参照するための前処理。
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "PDF ファイルのみアップロード可能です"}, status_code=400)

    # ファイル名のサニタイズ（ディレクトリトラバーサル防止）
    safe_name = Path(file.filename).name
    if not safe_name or safe_name.startswith("."):
        return JSONResponse({"error": "無効なファイル名です"}, status_code=400)

    content = await file.read()
    if len(content) > _UPLOAD_MAX_BYTES:
        return JSONResponse({"error": "ファイルサイズが上限（10MB）を超えています"}, status_code=413)

    # PDF 先頭バイト検証
    if not content[:5].startswith(b"%PDF-"):
        return JSONResponse({"error": "有効な PDF ファイルではありません"}, status_code=400)

    dest = _ALLOWED_DIR / safe_name
    dest.write_bytes(content)
    logger.info("PDF アップロード完了: %s (%d bytes)", safe_name, len(content))

    return JSONResponse({"filename": safe_name, "size": len(content)})
