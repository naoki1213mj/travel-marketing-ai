"""SSE チャットエンドポイント。Workflow の結果を SSE ストリームで返す。"""

import asyncio
import json
import logging
import random
import re
import secrets
import time
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from enum import StrEnum
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import TypedDict

from fastapi import APIRouter, BackgroundTasks, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings
from src.conversations import get_conversation, save_conversation
from src.middleware import check_prompt_shield, check_tool_response

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
_EMAIL_ADDRESS_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_MANAGER_APPROVAL_TOKEN_METADATA_KEY = "manager_approval_callback_token"
_BACKGROUND_UPDATES_PENDING_METADATA_KEY = "background_updates_pending"
_PIPELINE_TOTAL_STEPS = 5


class WorkflowSettings(TypedDict):
    """承認フローの設定。"""

    manager_approval_enabled: bool
    manager_email: str


class PendingApprovalContext(TypedDict):
    """承認待ちの企画書コンテキスト。"""

    user_input: str
    analysis_markdown: str
    plan_markdown: str
    model_settings: dict | None
    workflow_settings: WorkflowSettings | None
    approval_scope: str
    manager_callback_token: str | None


class AgentExecutionOutcome(TypedDict):
    """単一エージェント実行の結果。"""

    events: list[str]
    text: str
    success: bool
    latency_seconds: float
    tool_calls: int
    total_tokens: int


class PostCompletionUpdateContext(TypedDict):
    """完了後にバックグラウンドで継続する処理の入力。"""

    conversation_id: str
    review_input: str
    revised_plan_markdown: str
    brochure_html: str
    video_job_id: str | None


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


def _sanitize_optional_text(value: str | None) -> str:
    """空文字を許可する軽量サニタイズ。"""
    if value is None:
        return ""
    return _CONTROL_CHAR_RE.sub("", value).strip()


def _create_manager_callback_token() -> str:
    """外部 workflow callback 用の共有トークンを生成する。"""
    return secrets.token_urlsafe(32)


def _build_manager_callback_url(base_url: str, conversation_id: str) -> str:
    """manager approval callback URL を構築する。"""
    normalized_base_url = base_url.rstrip("/")
    encoded_conversation_id = urllib.parse.quote(conversation_id, safe="")
    return f"{normalized_base_url}/api/chat/{encoded_conversation_id}/manager-approval-callback"


def _build_manager_approval_url(base_url: str, conversation_id: str, approval_token: str) -> str:
    """上司向け承認ページ URL を構築する。"""
    normalized_base_url = base_url.rstrip("/")
    query = urllib.parse.urlencode({"manager_conversation_id": conversation_id})
    fragment = urllib.parse.urlencode({"manager_approval_token": approval_token})
    return f"{normalized_base_url}/?{query}#{fragment}"


def _extract_forwarded_header_value(value: str | None) -> str:
    """Forwarded header の先頭値を取り出す。"""
    if not value:
        return ""
    return value.split(",", 1)[0].strip()


def _build_public_base_url(request: Request) -> str:
    """リバースプロキシ配下でも公開 URL を正しく組み立てる。"""
    forwarded_proto = _sanitize_optional_text(_extract_forwarded_header_value(request.headers.get("x-forwarded-proto")))
    forwarded_host = _sanitize_optional_text(
        _extract_forwarded_header_value(request.headers.get("x-forwarded-host") or request.headers.get("host"))
    )
    if forwarded_host:
        scheme = forwarded_proto or request.url.scheme or "https"
        return f"{scheme}://{forwarded_host}".rstrip("/")
    return str(request.base_url).rstrip("/")


def _get_conversation_metadata(conversation: dict | None) -> dict[str, object]:
    """会話ドキュメントの metadata を安全に取り出す。"""
    if not isinstance(conversation, dict):
        return {}
    metadata = conversation.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    return dict(metadata)


def _get_manager_callback_token_from_conversation(conversation: dict | None) -> str:
    """保存済み会話 metadata から callback token を取得する。"""
    metadata = _get_conversation_metadata(conversation)
    value = metadata.get(_MANAGER_APPROVAL_TOKEN_METADATA_KEY)
    return _sanitize_optional_text(str(value) if value is not None else "")


def _has_background_updates_pending(conversation: dict | None) -> bool:
    """保存済み会話 metadata から background update の pending 状態を返す。"""
    metadata = _get_conversation_metadata(conversation)
    return _to_bool(metadata.get(_BACKGROUND_UPDATES_PENDING_METADATA_KEY))


def _build_conversation_metadata_for_save(
    conversation_id: str,
    existing_conversation: dict | None,
    conversation_status: str,
    background_updates_pending: bool | None = None,
) -> dict | None:
    """会話保存時の metadata を構築する。"""
    metadata = _get_conversation_metadata(existing_conversation)
    pending_context = _pending_approvals.get(conversation_id)
    pending_token = ""
    if pending_context is not None:
        pending_token = _sanitize_optional_text(pending_context.get("manager_callback_token"))

    if conversation_status == "awaiting_manager_approval":
        callback_token = pending_token or _get_manager_callback_token_from_conversation(existing_conversation)
        if callback_token:
            metadata[_MANAGER_APPROVAL_TOKEN_METADATA_KEY] = callback_token
    else:
        metadata.pop(_MANAGER_APPROVAL_TOKEN_METADATA_KEY, None)

    if background_updates_pending is True:
        metadata[_BACKGROUND_UPDATES_PENDING_METADATA_KEY] = True
    elif background_updates_pending is False:
        metadata.pop(_BACKGROUND_UPDATES_PENDING_METADATA_KEY, None)

    return metadata or None


def _to_bool(value: object) -> bool:
    """入力値を bool に正規化する。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(value, (int, float)):
        return bool(value)
    return False


def _sanitize_email_value(value: object) -> str:
    """メールアドレスを正規化し、形式が不正なら例外を送出する。"""
    email = _sanitize_optional_text(str(value) if value is not None else "")
    if not email:
        return ""
    if not _EMAIL_ADDRESS_RE.fullmatch(email):
        raise ValueError("上司メールアドレスの形式が不正です")
    return email


def _normalize_model_settings(raw_settings: dict | None) -> dict | None:
    """モデル設定だけを抽出して正規化する。"""
    if not isinstance(raw_settings, dict):
        return None

    normalized: dict[str, object] = {}
    for key in ("model", "temperature", "max_tokens", "top_p", "iq_search_results", "iq_score_threshold"):
        if key in raw_settings:
            normalized[key] = raw_settings[key]

    image_settings = raw_settings.get("image_settings")
    if isinstance(image_settings, dict):
        normalized["image_settings"] = {
            key: image_settings[key]
            for key in ("image_model", "image_quality", "image_width", "image_height")
            if key in image_settings
        }

    return normalized or None


def _normalize_workflow_settings(
    raw_settings: dict | None, raw_workflow_settings: dict | None
) -> WorkflowSettings | None:
    """ワークフロー設定を抽出して正規化する。"""
    source = (
        raw_workflow_settings
        if isinstance(raw_workflow_settings, dict)
        else raw_settings
        if isinstance(raw_settings, dict)
        else {}
    )
    enabled = _to_bool(source.get("manager_approval_enabled")) if isinstance(source, dict) else False
    if not enabled and isinstance(source, dict):
        enabled = _to_bool(source.get("managerApprovalEnabled"))

    email = ""
    if isinstance(source, dict):
        email = _sanitize_email_value(source.get("manager_email") or source.get("managerEmail"))

    if enabled and not email:
        raise ValueError("上司承認を有効化する場合は上司メールアドレスが必要です")
    if not enabled and not email:
        return None

    return {
        "manager_approval_enabled": enabled,
        "manager_email": email,
    }


def _validate_manager_approval_configuration(workflow_settings: WorkflowSettings | None) -> None:
    """上司承認の追加設定を検証する。"""
    if not workflow_settings or not workflow_settings.get("manager_approval_enabled"):
        return


def _build_approval_request_data(
    *,
    prompt: str,
    conversation_id: str,
    plan_markdown: str,
    model_settings: dict | None,
    workflow_settings: WorkflowSettings | None,
    approval_scope: str,
    manager_comment: str | None = None,
    manager_approval_url: str | None = None,
    manager_delivery_mode: str | None = None,
) -> dict:
    """approval_request の payload を共通生成する。"""
    data = {
        "prompt": prompt,
        "conversation_id": conversation_id,
        "plan_markdown": plan_markdown,
        "model_settings": model_settings,
        "approval_scope": approval_scope,
    }
    if workflow_settings:
        data["workflow_settings"] = workflow_settings
        if workflow_settings.get("manager_email"):
            data["manager_email"] = workflow_settings["manager_email"]
    if manager_comment:
        data["manager_comment"] = manager_comment
    if manager_approval_url:
        data["manager_approval_url"] = manager_approval_url
    if manager_delivery_mode:
        data["manager_delivery_mode"] = manager_delivery_mode
    return data


def _extract_manager_approval_token(request: Request, body_token: str | None = None) -> str:
    """body / header / query から manager approval token を抽出する。"""
    return (
        _sanitize_optional_text(body_token)
        or _sanitize_optional_text(request.headers.get("x-manager-approval-token"))
        or _sanitize_optional_text(request.query_params.get("token"))
    )


def _record_sse_event(collected_events: list[dict], event: str, start: float) -> None:
    """SSE 文字列を保存用イベント dict に変換して追加する。"""
    try:
        lines = event.strip().split("\n")
        ev_type = lines[0].replace("event: ", "") if lines else ""
        ev_data = json.loads(lines[1].replace("data: ", "")) if len(lines) > 1 else {}
        collected_events.append({"time": round(time.monotonic() - start, 2), "event": ev_type, "data": ev_data})
    except Exception as exc:
        logger.warning("SSE イベント収集のパースに失敗: %s", exc)


def _sse_to_event_dict(event: str, *, background_update: bool = False) -> dict | None:
    """SSE 文字列を会話保存用イベント dict に変換する。"""
    try:
        lines = event.strip().split("\n")
        ev_type = lines[0].replace("event: ", "") if lines else ""
        ev_data = json.loads(lines[1].replace("data: ", "")) if len(lines) > 1 else {}
    except Exception as exc:
        logger.warning("SSE イベントの変換に失敗: %s", exc)
        return None

    if background_update and isinstance(ev_data, dict):
        ev_data = {**ev_data, "background_update": True}

    return {"event": ev_type, "data": ev_data}


def _extract_committed_plan_versions(conversation: dict | None) -> list[dict[str, object]]:
    """保存済み会話から確定済み企画書バージョン一覧を抽出する。"""
    if not isinstance(conversation, dict):
        return []

    messages = conversation.get("messages")
    if not isinstance(messages, list):
        return []

    versions: list[dict[str, object]] = []
    latest_plan_markdown = ""

    for event in messages:
        if not isinstance(event, dict):
            continue
        event_name = event.get("event")
        data = event.get("data")
        if not isinstance(data, dict):
            continue

        if event_name == "text" and data.get("agent") in {"marketing-plan-agent", "plan-revision-agent"}:
            latest_plan_markdown = _sanitize_optional_text(str(data.get("content") or ""))
            continue

        if event_name != SSEEventType.DONE.value or not latest_plan_markdown:
            continue

        version_number = len(versions) + 1
        versions.append(
            {
                "version": version_number,
                "plan_title": _extract_plan_title(latest_plan_markdown),
                "plan_markdown": latest_plan_markdown,
            }
        )

    return versions


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
    model_settings: dict | None = None,
) -> AgentExecutionOutcome:
    """Agent4 が失敗したときに最低限の販促物を返す。"""
    from src.agents.brochure_gen import (
        _FALLBACK_IMAGE,
        generate_banner_image,
        generate_hero_image,
        pop_pending_images,
        set_current_conversation_id,
        set_current_image_settings,
    )

    title = _extract_plan_title(source_text)
    set_current_conversation_id(conversation_id)
    if model_settings and model_settings.get("image_settings"):
        set_current_image_settings(model_settings["image_settings"])
    await generate_hero_image(
        prompt="Bright family travel campaign hero image with resort atmosphere",
        destination=title,
        style="photorealistic",
    )
    await generate_banner_image(
        prompt=f"Travel promotion banner for {title}",
        platform="instagram",
    )
    pending_images = pop_pending_images(conversation_id)
    hero_image = pending_images.get("hero", _FALLBACK_IMAGE)
    banner_image = pending_images.get("banner_instagram", _FALLBACK_IMAGE)
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

    for tool_name in ["generate_hero_image", "generate_banner_image"]:
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
        last_data = events[-1].get("data", {})
        if isinstance(last_data, dict) and last_data.get("approval_scope") == "manager":
            return "awaiting_manager_approval"
        return "awaiting_approval"
    if last_event in {SSEEventType.ERROR, SSEEventType.ERROR.value}:
        return "error"
    return "completed"


_TOOL_CALL_TYPE_MAP = {
    "code_interpreter_call": "code_interpreter",
    "web_search_call": "web_search",
    "bing_grounding_call": "web_search",
}


def _merge_tool_names(*tool_groups: list[str]) -> list[str]:
    """ツール名の順序を保って重複排除する。"""
    merged: list[str] = []
    for group in tool_groups:
        for tool_name in group:
            if tool_name and tool_name not in merged:
                merged.append(tool_name)
    return merged


def _collect_result_outputs(result: object) -> list[object]:
    """agent.run() の戻り値から tool 呼び出し候補を再帰的に収集する。"""
    if result is None:
        return []

    collected: list[object] = []
    queue: list[object] = []

    contents = getattr(result, "contents", None)
    if isinstance(contents, list):
        queue.extend(contents)

    try:
        outputs = result.get_outputs() if hasattr(result, "get_outputs") else []
    except AttributeError, TypeError, RuntimeError, OSError:
        outputs = []

    if isinstance(outputs, list):
        queue.extend(outputs)

    while queue:
        item = queue.pop(0)
        if isinstance(item, list):
            queue[:0] = item
            continue
        collected.append(item)

        nested_contents = getattr(item, "contents", None)
        if isinstance(nested_contents, list):
            queue.extend(nested_contents)

        nested_output = getattr(item, "output", None)
        if isinstance(nested_output, list):
            queue.extend(nested_output)

    return collected


def _extract_tool_names(result: object, agent_name: str, result_text: str) -> list[str]:
    """Responses 出力から実際に使われたツール名を推定する。"""
    tool_names: list[str] = []

    for output in _collect_result_outputs(result):
        output_type = getattr(output, "type", "")
        if not isinstance(output_type, str) or not output_type:
            continue

        if output_type == "function_call":
            name = getattr(output, "name", None)
            if not isinstance(name, str) or not name:
                function_obj = getattr(output, "function", None)
                name = getattr(function_obj, "name", None)
            if isinstance(name, str) and name:
                tool_names.append(name)
            continue

        mapped = _TOOL_CALL_TYPE_MAP.get(output_type)
        if mapped:
            tool_names.append(mapped)

    if agent_name == "video-gen-agent" and result_text.strip():
        tool_names.append("generate_promo_video")

    return _merge_tool_names(tool_names)


async def _load_pending_approval_context(conversation_id: str) -> PendingApprovalContext | None:
    """承認待ちコンテキストをメモリまたは保存済み会話から復元する。"""
    context = _pending_approvals.get(conversation_id)
    if context:
        return context

    conversation = await get_conversation(conversation_id)
    if not conversation:
        return None
    if conversation.get("status") not in {"awaiting_approval", "awaiting_manager_approval"}:
        return None

    analysis_markdown = ""
    plan_markdown = ""
    model_settings: dict | None = None
    workflow_settings: WorkflowSettings | None = None
    approval_scope = "manager" if conversation.get("status") == "awaiting_manager_approval" else "user"
    manager_callback_token = _get_manager_callback_token_from_conversation(conversation)
    for message in conversation.get("messages", []):
        event_name = message.get("event")
        data = message.get("data", {})
        if event_name == SSEEventType.APPROVAL_REQUEST.value:
            plan_markdown = data.get("plan_markdown", plan_markdown)
            event_model_settings = data.get("model_settings")
            if isinstance(event_model_settings, dict):
                model_settings = event_model_settings
            event_workflow_settings = data.get("workflow_settings")
            if isinstance(event_workflow_settings, dict):
                workflow_settings = {
                    "manager_approval_enabled": _to_bool(event_workflow_settings.get("manager_approval_enabled")),
                    "manager_email": _sanitize_optional_text(event_workflow_settings.get("manager_email")),
                }
            if data.get("approval_scope") == "manager":
                approval_scope = "manager"
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
        "model_settings": model_settings,
        "workflow_settings": workflow_settings,
        "approval_scope": approval_scope,
        "manager_callback_token": manager_callback_token or None,
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

    # brochure-gen-agent の場合、side-channel の conversation_id と画像設定を設定
    if agent_name == "brochure-gen-agent":
        from src.agents.brochure_gen import set_current_conversation_id, set_current_image_settings

        set_current_conversation_id(conversation_id)
        # model_settings 内の image_settings を画像コンテキスト変数にセット
        if model_settings and model_settings.get("image_settings"):
            set_current_image_settings(model_settings["image_settings"])
    if agent_name == "video-gen-agent":
        from src.agents.video_gen import set_current_conversation_id

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
                    model_settings=model_settings,
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
    tool_names = _extract_tool_names(result, agent_name, result_text)

    tool_shield = await check_tool_response(result_text)
    if not tool_shield.is_safe:
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

    events.extend(_build_content_events(agent_name, result_text))

    # Side-channel 画像の取得（brochure-gen-agent のツールが画像を side-channel に保存する）
    if agent_name == "brochure-gen-agent":
        from src.agents.brochure_gen import pop_pending_images, set_current_conversation_id

        set_current_conversation_id(conversation_id)
        pending = pop_pending_images(conversation_id)
        brochure_tools: list[str] = []
        if "hero" in pending:
            brochure_tools.append("generate_hero_image")
        if any(key.startswith("banner_") for key in pending):
            brochure_tools.append("generate_banner_image")
        tool_names = _merge_tool_names(tool_names, brochure_tools)

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
        if ci_images:
            tool_names = _merge_tool_names(tool_names, ["code_interpreter"])
        for ci_img in ci_images:
            events.append(
                format_sse(
                    SSEEventType.IMAGE,
                    {"url": ci_img["url"], "alt": ci_img["alt"], "agent": agent_name},
                )
            )

    for tool_name in tool_names:
        events.append(
            format_sse(
                SSEEventType.TOOL_EVENT,
                {"tool": tool_name, "status": "completed", "agent": agent_name},
            )
        )

    events.append(
        format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": agent_name, "status": "completed", "step": step, "total_steps": total_steps},
        )
    )

    elapsed = round(time.monotonic() - start_time, 1)
    tool_calls = len(tool_names)

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

    message: str = Field(..., min_length=1)
    conversation_id: str | None = Field(None, max_length=100)
    settings: dict | None = Field(None, description="モデルパラメータ設定")
    workflow_settings: dict | None = Field(None, description="承認フロー設定")

    @field_validator("message")
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        """前後空白除去・制御文字除去・空文字拒否"""
        return _sanitize_text(v)


class ApproveRequest(BaseModel):
    """承認/修正リクエスト"""

    conversation_id: str = Field(..., max_length=100)
    response: str = Field(..., min_length=1)

    @field_validator("response")
    @classmethod
    def sanitize_response(cls, v: str) -> str:
        """前後空白除去・制御文字除去・空文字拒否"""
        return _sanitize_text(v)


class ManagerApprovalCallbackRequest(BaseModel):
    """Logic Apps からの上司承認コールバック。"""

    conversation_id: str | None = Field(None, max_length=100)
    approved: bool
    comment: str | None = Field(None, max_length=2000)
    approver_email: str | None = Field(None, max_length=320)
    callback_token: str | None = Field(None, max_length=255)

    @field_validator("comment")
    @classmethod
    def sanitize_comment(cls, v: str | None) -> str | None:
        """コメントは空を許容しつつ制御文字だけ除去する。"""
        cleaned = _sanitize_optional_text(v)
        return cleaned or None

    @field_validator("approver_email")
    @classmethod
    def sanitize_approver_email(cls, v: str | None) -> str | None:
        """承認者メールアドレスを正規化する。"""
        email = _sanitize_email_value(v)
        return email or None

    @field_validator("callback_token")
    @classmethod
    def sanitize_callback_token(cls, v: str | None) -> str | None:
        """callback token を正規化する。"""
        token = _sanitize_optional_text(v)
        return token or None


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
            "approval_scope": "user",
            "manager_callback_token": None,
        }
        yield format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": "approval", "status": "running", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
        )
        yield format_sse(
            SSEEventType.APPROVAL_REQUEST,
            _build_approval_request_data(
                prompt="修正した企画書を確認してください。承認する場合は「承認」、さらに修正したい場合は修正内容を入力してください。",
                conversation_id=conversation_id,
                plan_markdown=outcome["text"],
                model_settings=pending_context.get("model_settings"),
                workflow_settings=pending_context.get("workflow_settings"),
                approval_scope="user",
            ),
        )
        return

    text_lower = refine_text.lower()
    # 評価フィードバック（品質評価結果に基づく改善指示）は常に企画書修正として処理
    is_eval_feedback = "品質評価" in refine_text or "evaluation" in text_lower
    if is_eval_feedback:
        # 評価フィードバック → 企画書を再生成して承認フローに再突入
        conversation = await get_conversation(conversation_id)
        original_plan = ""
        analysis_markdown = ""
        model_settings: dict | None = None
        workflow_settings: WorkflowSettings | None = None
        user_input = ""
        if conversation:
            user_input = conversation.get("input", "")
            for msg in conversation.get("messages", []):
                data = msg.get("data", {})
                if msg.get("event") == SSEEventType.APPROVAL_REQUEST.value and isinstance(
                    data.get("model_settings"), dict
                ):
                    model_settings = data["model_settings"]
                if msg.get("event") == SSEEventType.APPROVAL_REQUEST.value and isinstance(
                    data.get("workflow_settings"), dict
                ):
                    workflow_settings = {
                        "manager_approval_enabled": _to_bool(data["workflow_settings"].get("manager_approval_enabled")),
                        "manager_email": _sanitize_optional_text(data["workflow_settings"].get("manager_email")),
                    }
                if msg.get("event") == "text" and data.get("agent") == "data-search-agent" and not analysis_markdown:
                    analysis_markdown = data.get("content", "")
                if msg.get("event") == "text" and data.get("agent") == "marketing-plan-agent":
                    original_plan = data.get("content", "")

        revision_prompt = (
            f"以下の旅行企画書を、品質評価のフィードバックに基づいて改善してください。\n\n"
            f"## 元の依頼\n{user_input}\n\n"
            f"## 現在の企画書\n{original_plan}\n\n"
            f"## 改善指示\n{refine_text}"
        )

        outcome = await _execute_agent(
            agent_name="marketing-plan-agent",
            agent_step=2,
            user_input=revision_prompt,
            conversation_id=conversation_id,
            model_settings=model_settings,
        )
        for event in outcome["events"]:
            yield event
        if not outcome["success"]:
            return

        # 承認コンテキストを構築して承認フローに再突入
        _pending_approvals[conversation_id] = {
            "user_input": user_input,
            "analysis_markdown": analysis_markdown,
            "plan_markdown": outcome["text"],
            "model_settings": model_settings,
            "workflow_settings": workflow_settings,
            "approval_scope": "user",
            "manager_callback_token": None,
        }
        yield format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": "approval", "status": "running", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
        )
        yield format_sse(
            SSEEventType.APPROVAL_REQUEST,
            _build_approval_request_data(
                prompt="改善した企画書を確認してください。承認する場合は「承認」、さらに修正したい場合は修正内容を入力してください。",
                conversation_id=conversation_id,
                plan_markdown=outcome["text"],
                model_settings=model_settings,
                workflow_settings=workflow_settings,
                approval_scope="user",
            ),
        )
        return

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
    ignored_headings = {
        "タイトル",
        "キャッチコピー",
        "ターゲットペルソナ",
        "プラン概要",
        "差別化ポイント",
        "改善ポイント",
        "販促チャネル",
        "KPI",
    }
    for line in lines[:30]:
        line = line.strip().lstrip("#").strip()
        if not line or line.startswith("|") or line.startswith("-") or line.startswith("[参考パンフレット:"):
            continue
        if line.rstrip(":：") in ignored_headings:
            continue
        normalized = line.replace("**", "").replace("`", "").strip()
        if normalized:
            summary_parts.append(normalized.rstrip("。"))
        if len("。".join(summary_parts)) > 240:
            break
    if not summary_parts:
        return plan_text[:240]
    return "。".join(summary_parts)[:240]


async def _post_approval_events(
    user_response: str,
    conversation_id: str,
    base_url: str | None = None,
    approval_context: PendingApprovalContext | None = None,
    register_background_job: Callable[[PostCompletionUpdateContext], None] | None = None,
):
    """承認後に Agent3 → Agent4 を実行する SSE イベント"""
    settings = get_settings()

    if not settings["project_endpoint"]:
        async for event in _mock_post_approval_events(conversation_id):
            yield event
        return

    context = approval_context or await _load_pending_approval_context(conversation_id)
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
    workflow_settings = context.get("workflow_settings")
    approval_scope = context.get("approval_scope", "user")
    manager_callback_token = context.get("manager_callback_token")
    regulation_text = ""
    revised_plan_markdown = context["plan_markdown"]
    hero_image_task = None
    destination_text = None
    video_outcome_task = None

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "approval", "status": "completed", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
    )

    if approval_scope != "manager":
        yield format_sse(
            SSEEventType.AGENT_PROGRESS,
            {"agent": "regulation-check-agent", "status": "running", "step": 4, "total_steps": _PIPELINE_TOTAL_STEPS},
        )

        regulation_input = context["plan_markdown"]
        dest_match = re.search(r"(?:旅行先|目的地|プラン名)[：:]\s*(.+?)[\n。]", regulation_input)
        if dest_match:
            destination_text = dest_match.group(1).strip()
        else:
            for place in ["北海道", "沖縄", "京都", "東京", "九州", "東北", "四国", "北陸"]:
                if place in regulation_input:
                    destination_text = place
                    break

        if destination_text and settings["project_endpoint"]:
            from src.agents.brochure_gen import set_current_conversation_id, set_current_image_settings

            set_current_conversation_id(conversation_id)
            ctx_model_settings = context.get("model_settings")
            if ctx_model_settings and ctx_model_settings.get("image_settings"):
                set_current_image_settings(ctx_model_settings["image_settings"])

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
        regulation_text = regulation_outcome["text"]

        revision_input = f"## 元の企画書\n\n{context['plan_markdown']}\n\n## 規制チェック結果\n\n{regulation_text}"
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
        revised_plan_markdown = revision_outcome["text"] or context["plan_markdown"]

        if workflow_settings and workflow_settings.get("manager_approval_enabled"):
            manager_callback_token = manager_callback_token or _create_manager_callback_token()
            if not base_url:
                yield format_sse(
                    SSEEventType.ERROR,
                    {
                        "message": "上司承認ページの URL を生成できませんでした。アプリの公開 URL を確認してください。",
                        "code": "MANAGER_APPROVAL_URL_BUILD_FAILED",
                    },
                )
                return

            manager_callback_url = _build_manager_callback_url(base_url, conversation_id)
            manager_approval_url = _build_manager_approval_url(base_url, conversation_id, manager_callback_token)
            manager_delivery_mode = "manual"
            manager_prompt = "修正版企画書の上司承認リンクを発行しました。リンクを上司へ共有してください。"

            if settings["manager_approval_trigger_url"]:
                try:
                    await _submit_manager_approval_request(
                        conversation_id=conversation_id,
                        plan_markdown=revised_plan_markdown,
                        workflow_settings=workflow_settings,
                        manager_callback_url=manager_callback_url,
                        manager_callback_token=manager_callback_token,
                        manager_approval_url=manager_approval_url,
                    )
                    manager_delivery_mode = "workflow"
                    manager_prompt = "修正版企画書を上司へ通知しました。承認ページまたは通知内リンクから承認できます。"
                except (ValueError, OSError) as exc:
                    logger.warning(
                        "上司承認 notification workflow の送信に失敗。共有リンクへフォールバックします: %s", exc
                    )
                    manager_prompt = "通知 workflow の送信に失敗しました。承認ページのリンクを上司へ共有してください。"

            _pending_approvals[conversation_id] = {
                **context,
                "plan_markdown": revised_plan_markdown,
                "approval_scope": "manager",
                "manager_callback_token": manager_callback_token,
            }
            yield format_sse(
                SSEEventType.APPROVAL_REQUEST,
                _build_approval_request_data(
                    prompt=manager_prompt,
                    conversation_id=conversation_id,
                    plan_markdown=revised_plan_markdown,
                    model_settings=context.get("model_settings"),
                    workflow_settings=workflow_settings,
                    approval_scope="manager",
                    manager_approval_url=manager_approval_url,
                    manager_delivery_mode=manager_delivery_mode,
                ),
            )
            return

    if destination_text is None:
        dest_match = re.search(r"(?:旅行先|目的地|プラン名)[：:]\s*(.+?)[\n。]", revised_plan_markdown)
        if dest_match:
            destination_text = dest_match.group(1).strip()
        else:
            for place in ["北海道", "沖縄", "京都", "東京", "九州", "東北", "四国", "北陸"]:
                if place in revised_plan_markdown:
                    destination_text = place
                    break

    video_summary = _extract_plan_summary(revised_plan_markdown)
    if video_summary:
        video_outcome_task = asyncio.create_task(
            _execute_agent(
                agent_name="video-gen-agent",
                agent_step=5,
                user_input=video_summary,
                conversation_id=conversation_id,
                model_settings=context.get("model_settings"),
            )
        )

    # 規制チェック+修正完了 → 販促物生成フェーズへ即座に遷移（UI の待ち時間解消）
    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "brochure-gen-agent", "status": "running", "step": 5, "total_steps": _PIPELINE_TOTAL_STEPS},
    )

    # Agent4 への入力は修正版企画書
    brochure_input = revised_plan_markdown

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
        if video_outcome_task is not None:
            video_outcome_task.cancel()
        return
    total_tool_calls += brochure_outcome["tool_calls"]
    total_tokens_sum += brochure_outcome.get("total_tokens", 0)

    if video_outcome_task is not None:
        video_outcome = await video_outcome_task
        for event in video_outcome["events"]:
            yield event
        total_tool_calls += video_outcome["tool_calls"]
        total_tokens_sum += video_outcome.get("total_tokens", 0)

    from src.agents.video_gen import pop_pending_video_job

    video_job = pop_pending_video_job(conversation_id)
    video_job_id = _sanitize_optional_text(video_job.get("job_id")) if isinstance(video_job, dict) else ""

    if video_job_id:
        yield format_sse(
            SSEEventType.TEXT,
            {
                "content": "販促動画をバックグラウンドで生成しています。動画タブは完了後に自動更新されます。",
                "agent": "video-gen-agent",
                "content_type": "text",
            },
        )

    review_input = "\n\n".join(
        part
        for part in [
            context["analysis_markdown"],
            revised_plan_markdown,
            regulation_text,
            brochure_outcome["text"],
        ]
        if part
    )
    brochure_html = _extract_brochure_html(brochure_outcome["text"]) or ""
    background_updates_pending = bool(video_job_id or review_input.strip() or settings["logic_app_callback_url"])

    if background_updates_pending and register_background_job is None:
        if video_job_id:
            from src.agents.video_gen import poll_video_job

            video_url = await poll_video_job(video_job_id, max_wait=120)
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

        for event in await _maybe_run_quality_review(review_input):
            yield event

        await _trigger_logic_app(
            conversation_id=conversation_id,
            plan_markdown=revised_plan_markdown,
            brochure_html=brochure_html,
        )
        background_updates_pending = False
    elif background_updates_pending and register_background_job is not None:
        register_background_job(
            {
                "conversation_id": conversation_id,
                "review_input": review_input,
                "revised_plan_markdown": revised_plan_markdown,
                "brochure_html": brochure_html,
                "video_job_id": video_job_id or None,
            }
        )

    _pending_approvals.pop(conversation_id, None)

    yield format_sse(
        SSEEventType.DONE,
        {
            "conversation_id": conversation_id,
            "background_updates_pending": background_updates_pending,
            "metrics": {
                "latency_seconds": round(time.monotonic() - approval_start, 1),
                "tool_calls": total_tool_calls,
                "total_tokens": total_tokens_sum,
            },
        },
    )


async def _append_post_completion_updates(
    conversation_id: str,
    update_context: PostCompletionUpdateContext,
) -> None:
    """完了後の動画・品質レビュー・通知をバックグラウンドで追記する。"""
    existing_conversation = await get_conversation(conversation_id)
    if not existing_conversation:
        logger.warning("background update 対象の会話が見つかりません: %s", conversation_id)
        return

    appended_events: list[dict] = []

    video_job_id = _sanitize_optional_text(update_context.get("video_job_id"))
    if video_job_id:
        from src.agents.video_gen import poll_video_job

        video_url = await poll_video_job(video_job_id, max_wait=120)
        if video_url and video_url.startswith("https://"):
            appended_events.append(
                {
                    "event": SSEEventType.TEXT.value,
                    "data": {
                        "content": video_url,
                        "agent": "video-gen-agent",
                        "content_type": "video",
                        "background_update": True,
                    },
                }
            )
        elif video_url:
            logger.warning("Photo Avatar: 無効な background video_url を無視: %s", video_url[:100])

    review_input = _sanitize_optional_text(update_context.get("review_input"))
    if review_input:
        for event in await _maybe_run_quality_review(review_input):
            event_dict = _sse_to_event_dict(event, background_update=True)
            if event_dict is not None:
                appended_events.append(event_dict)

    await _trigger_logic_app(
        conversation_id=conversation_id,
        plan_markdown=update_context["revised_plan_markdown"],
        brochure_html=update_context["brochure_html"],
    )

    merged_messages = [*existing_conversation.get("messages", []), *appended_events]
    conversation_status = _conversation_status_from_events(merged_messages)
    await save_conversation(
        conversation_id=conversation_id,
        user_input=existing_conversation.get("input", ""),
        events=merged_messages,
        metrics=_build_conversation_metadata_for_save(
            conversation_id,
            existing_conversation,
            conversation_status,
            background_updates_pending=False,
        ),
        status=conversation_status,
    )


async def _append_post_completion_updates_safe(
    conversation_id: str,
    update_context: PostCompletionUpdateContext,
) -> None:
    """完了後の background update を安全に実行し、必ず pending 状態を解除する。"""
    try:
        await _append_post_completion_updates(conversation_id, update_context)
    except Exception:
        logger.exception("完了後の background update に失敗: conversation_id=%s", conversation_id)
        existing_conversation = await get_conversation(conversation_id)
        if not existing_conversation:
            return

        await save_conversation(
            conversation_id=conversation_id,
            user_input=existing_conversation.get("input", ""),
            events=existing_conversation.get("messages", []),
            metrics=_build_conversation_metadata_for_save(
                conversation_id,
                existing_conversation,
                str(existing_conversation.get("status", "completed")),
                background_updates_pending=False,
            ),
            status=str(existing_conversation.get("status", "completed")),
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
            "request_type": "post_approval_actions",
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


async def _submit_manager_approval_request(
    conversation_id: str,
    plan_markdown: str,
    workflow_settings: WorkflowSettings,
    manager_callback_url: str | None = None,
    manager_callback_token: str = "",
    manager_approval_url: str = "",
) -> None:
    """上司承認 workflow に上司承認依頼を送信する。"""
    settings = get_settings()
    callback_url = settings["manager_approval_trigger_url"]
    if not callback_url:
        raise ValueError("MANAGER_APPROVAL_TRIGGER_URL が未設定です")

    manager_email = workflow_settings.get("manager_email", "")
    if not manager_email:
        raise ValueError("上司メールアドレスが未設定です")
    if not manager_callback_token:
        raise ValueError("callback token が未設定です")

    payload = json.dumps(
        {
            "request_type": "manager_approval",
            "plan_title": _extract_plan_title(plan_markdown),
            "plan_markdown": plan_markdown,
            "conversation_id": conversation_id,
            "manager_email": manager_email,
            "manager_callback_url": manager_callback_url or "",
            "manager_callback_token": manager_callback_token,
            "manager_approval_url": manager_approval_url,
        },
        ensure_ascii=False,
    ).encode("utf-8")

    req = urllib.request.Request(
        callback_url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    await asyncio.to_thread(urllib.request.urlopen, req, timeout=15)
    logger.info("上司承認依頼を送信: conversation_id=%s manager=%s", conversation_id, manager_email)


async def _continue_after_manager_approval(conversation_id: str) -> None:
    """上司承認後にバックグラウンドで成果物生成を再開する。"""
    await _continue_after_manager_approval_safe(conversation_id)


async def _continue_after_manager_approval_safe(
    conversation_id: str,
    approval_context: PendingApprovalContext | None = None,
) -> None:
    """上司承認後の継続処理を例外ログ付きで実行する。"""
    try:
        await _run_manager_approval_continuation(conversation_id, approval_context)
    except Exception:
        logger.exception("上司承認後の継続処理に失敗: conversation_id=%s", conversation_id)
        existing_conversation = await get_conversation(conversation_id)
        if not existing_conversation:
            return

        error_events: list[dict] = []
        _record_sse_event(
            error_events,
            format_sse(
                SSEEventType.ERROR,
                {
                    "message": "上司承認後の継続処理に失敗しました。会話を再読み込みして再度お試しください。",
                    "code": "MANAGER_APPROVAL_CONTINUATION_FAILED",
                },
            ),
            time.monotonic(),
        )
        merged_messages = [*existing_conversation.get("messages", []), *error_events]
        conversation_status = _conversation_status_from_events(merged_messages)
        await save_conversation(
            conversation_id=conversation_id,
            user_input=existing_conversation.get("input", ""),
            events=merged_messages,
            metrics=_build_conversation_metadata_for_save(
                conversation_id,
                existing_conversation,
                conversation_status,
            ),
            status=conversation_status,
        )


async def _run_manager_approval_continuation(
    conversation_id: str,
    approval_context: PendingApprovalContext | None = None,
) -> None:
    """上司承認後にバックグラウンドで成果物生成を再開する。"""
    existing_conversation = await get_conversation(conversation_id)
    if not existing_conversation:
        logger.warning("上司承認後の会話が見つかりません: %s", conversation_id)
        return

    collected_events: list[dict] = []
    start = time.monotonic()
    background_update_jobs: list[PostCompletionUpdateContext] = []

    def _register_background_job(update_context: PostCompletionUpdateContext) -> None:
        background_update_jobs.append(update_context)

    async for event in _post_approval_events(
        "承認",
        conversation_id,
        approval_context=approval_context,
        register_background_job=_register_background_job,
    ):
        _record_sse_event(collected_events, event, start)

    merged_messages = [*existing_conversation.get("messages", []), *collected_events]
    conversation_status = _conversation_status_from_events(merged_messages)
    await save_conversation(
        conversation_id=conversation_id,
        user_input=existing_conversation.get("input", ""),
        events=merged_messages,
        metrics=_build_conversation_metadata_for_save(
            conversation_id,
            existing_conversation,
            conversation_status,
            background_updates_pending=bool(background_update_jobs),
        ),
        status=conversation_status,
    )

    for update_job in background_update_jobs:
        asyncio.create_task(_append_post_completion_updates_safe(conversation_id, update_job))


# --- エンドポイント ---


async def workflow_event_generator(
    user_input: str,
    conversation_id: str,
    model_settings: dict | None = None,
    workflow_settings: WorkflowSettings | None = None,
):
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
        "workflow_settings": workflow_settings,
        "approval_scope": "user",
        "manager_callback_token": None,
    }

    yield format_sse(
        SSEEventType.AGENT_PROGRESS,
        {"agent": "approval", "status": "running", "step": 3, "total_steps": _PIPELINE_TOTAL_STEPS},
    )
    yield format_sse(
        SSEEventType.APPROVAL_REQUEST,
        _build_approval_request_data(
            prompt="上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
            conversation_id=conversation_id,
            plan_markdown=plan_outcome["text"],
            model_settings=model_settings,
            workflow_settings=workflow_settings,
            approval_scope="user",
        ),
    )


@router.post("/chat")
@limiter.limit("10/minute")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """チャットメッセージを受け取り、SSE ストリームでパイプライン結果を返す"""
    conversation_id = body.conversation_id or str(uuid.uuid4())
    try:
        normalized_model_settings = _normalize_model_settings(body.settings)
        normalized_workflow_settings = _normalize_workflow_settings(body.settings, body.workflow_settings)
        _validate_manager_approval_configuration(normalized_workflow_settings)
    except ValueError as exc:
        return StreamingResponse(
            iter(
                [
                    format_sse(
                        SSEEventType.ERROR,
                        {"message": str(exc), "code": "INVALID_SETTINGS"},
                    )
                ]
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 入力ガード
    shield_result = await check_prompt_shield(body.message)

    async def guarded_generator():
        collected_events: list[dict] = []
        start = time.monotonic()

        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {
                    "message": "入力が注入ガードによりブロックされました",
                    "code": "INPUT_GUARD_BLOCKED",
                },
            )
            return

        async def _collect_and_yield(gen):
            async for event in gen:
                _record_sse_event(collected_events, event, start)
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
                    workflow_event_generator(
                        body.message,
                        conversation_id,
                        normalized_model_settings,
                        normalized_workflow_settings,
                    )
                ):
                    yield event
            else:
                async for event in _collect_and_yield(mock_event_generator(body.message, conversation_id)):
                    yield event

        # 会話を非同期で保存（レスポンスには影響しない）
        try:
            conversation_status = _conversation_status_from_events(collected_events)
            existing_conversation = await get_conversation(conversation_id)
            await save_conversation(
                conversation_id,
                body.message,
                collected_events,
                metrics=_build_conversation_metadata_for_save(
                    conversation_id,
                    existing_conversation,
                    conversation_status,
                ),
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
async def approve(
    thread_id: str, request: Request, body: ApproveRequest, background_tasks: BackgroundTasks
) -> StreamingResponse:
    """承認/修正レスポンスを受け取り、後続のパイプライン結果を SSE で返す"""
    # 入力ガード（承認レスポンスにも適用）
    shield_result = await check_prompt_shield(body.response)
    is_approved = _is_approval_response(body.response)
    background_update_jobs: list[PostCompletionUpdateContext] = []

    def _register_background_job(update_context: PostCompletionUpdateContext) -> None:
        background_update_jobs.append(update_context)

    async def approval_event_generator():
        collected_events: list[dict] = []
        start = time.monotonic()

        if not shield_result.is_safe:
            yield format_sse(
                SSEEventType.ERROR,
                {"message": "入力が注入ガードによりブロックされました", "code": "INPUT_GUARD_BLOCKED"},
            )
            return

        async def _collect_and_yield(gen):
            async for event in gen:
                _record_sse_event(collected_events, event, start)
                yield event

        existing_conversation = await get_conversation(thread_id)
        base_url = _build_public_base_url(request)
        if is_approved:
            async for event in _collect_and_yield(
                _post_approval_events(
                    body.response,
                    thread_id,
                    base_url,
                    register_background_job=_register_background_job,
                )
            ):
                yield event
        else:
            async for event in _collect_and_yield(_refine_events(body.response, thread_id)):
                yield event

        try:
            previous_messages = existing_conversation.get("messages", []) if existing_conversation else []
            merged_messages = [*previous_messages, *collected_events]
            conversation_status = _conversation_status_from_events(merged_messages)
            await save_conversation(
                conversation_id=thread_id,
                user_input=existing_conversation.get("input", body.response)
                if existing_conversation
                else body.response,
                events=merged_messages,
                metrics=_build_conversation_metadata_for_save(
                    thread_id,
                    existing_conversation,
                    conversation_status,
                    background_updates_pending=bool(background_update_jobs),
                ),
                status=conversation_status,
            )
        except (ValueError, OSError) as exc:
            logger.debug("承認系会話の保存に失敗（非致命的）: %s", exc)
        except RuntimeError, TypeError:
            logger.debug("承認系会話の保存に失敗（非致命的）", exc_info=True)

        for update_job in background_update_jobs:
            background_tasks.add_task(_append_post_completion_updates_safe, thread_id, update_job)

    return StreamingResponse(
        approval_event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/{thread_id}/manager-approval-request")
@limiter.limit("30/minute")
async def get_manager_approval_request(thread_id: str, request: Request) -> JSONResponse:
    """上司向け承認ページで表示する承認対象データを返す。"""
    context = await _load_pending_approval_context(thread_id)
    if context is None or context.get("approval_scope") != "manager":
        return JSONResponse(status_code=404, content={"error": "manager approval context not found"})

    existing_conversation = await get_conversation(thread_id)
    previous_versions = _extract_committed_plan_versions(existing_conversation)

    callback_token = _extract_manager_approval_token(request)
    expected_token = _sanitize_optional_text(context.get("manager_callback_token"))
    if not expected_token or callback_token != expected_token:
        return JSONResponse(status_code=403, content={"error": "invalid manager approval token"})

    workflow_settings = context.get("workflow_settings") or {}
    plan_markdown = context["plan_markdown"]
    return JSONResponse(
        content={
            "conversation_id": thread_id,
            "current_version": len(previous_versions) + 1,
            "plan_title": _extract_plan_title(plan_markdown),
            "plan_markdown": plan_markdown,
            "manager_email": workflow_settings.get("manager_email"),
            "previous_versions": previous_versions,
        },
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


@router.post("/chat/{thread_id}/manager-approval-callback")
@limiter.limit("20/minute")
async def manager_approval_callback(
    thread_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    body: ManagerApprovalCallbackRequest,
) -> JSONResponse:
    """上司承認 workflow からの承認結果を受け取り、後続処理を再開する。"""
    if body.conversation_id and body.conversation_id != thread_id:
        return JSONResponse(status_code=400, content={"error": "conversation_id mismatch"})

    context = await _load_pending_approval_context(thread_id)
    if context is None or context.get("approval_scope") != "manager":
        return JSONResponse(status_code=404, content={"error": "manager approval context not found"})

    callback_token = _extract_manager_approval_token(request, body.callback_token)
    expected_token = _sanitize_optional_text(context.get("manager_callback_token"))
    if not expected_token or callback_token != expected_token:
        return JSONResponse(status_code=403, content={"error": "invalid manager approval token"})

    if body.approved:
        existing_conversation = await get_conversation(thread_id)
        if existing_conversation:
            await save_conversation(
                conversation_id=thread_id,
                user_input=existing_conversation.get("input", context.get("user_input", "")),
                events=existing_conversation.get("messages", []),
                metrics=_build_conversation_metadata_for_save(
                    thread_id,
                    existing_conversation,
                    "running",
                ),
                status="running",
            )
        _pending_approvals.pop(thread_id, None)
        background_tasks.add_task(_continue_after_manager_approval_safe, thread_id, context)
        return JSONResponse(content={"status": "accepted", "conversation_id": thread_id})

    existing_conversation = await get_conversation(thread_id)
    manager_comment = body.comment or "上司から差し戻しされました。内容を確認して修正してください。"
    workflow_settings = context.get("workflow_settings")
    _pending_approvals[thread_id] = {
        **context,
        "approval_scope": "user",
        "manager_callback_token": None,
    }

    reopened_events: list[dict] = []
    reopened_event = format_sse(
        SSEEventType.APPROVAL_REQUEST,
        _build_approval_request_data(
            prompt="上司から差し戻しがありました。コメントを確認し、修正するか承認してください。",
            conversation_id=thread_id,
            plan_markdown=context["plan_markdown"],
            model_settings=context.get("model_settings"),
            workflow_settings=workflow_settings,
            approval_scope="user",
            manager_comment=manager_comment,
        ),
    )
    _record_sse_event(reopened_events, reopened_event, time.monotonic())

    previous_messages = existing_conversation.get("messages", []) if existing_conversation else []
    merged_messages = [*previous_messages, *reopened_events]
    conversation_status = _conversation_status_from_events(merged_messages)
    await save_conversation(
        conversation_id=thread_id,
        user_input=existing_conversation.get("input", context["user_input"])
        if existing_conversation
        else context["user_input"],
        events=merged_messages,
        metrics=_build_conversation_metadata_for_save(
            thread_id,
            existing_conversation,
            conversation_status,
        ),
        status=conversation_status,
    )
    return JSONResponse(content={"status": "reopened", "conversation_id": thread_id})


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
