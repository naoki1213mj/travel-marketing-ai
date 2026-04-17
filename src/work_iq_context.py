"""Work IQ brief retrieval via Microsoft Graph Copilot Chat API."""

import json
import logging
import re
from collections import Counter
from typing import Any, NotRequired, TypedDict

import httpx

from src.config import get_settings
from src.http_client import get_http_client
from src.work_iq_session import WorkIQSourceMetadata

logger = logging.getLogger(__name__)

_GRAPH_CONVERSATIONS_URL = "https://graph.microsoft.com/beta/copilot/conversations"
_DEFAULT_TIMEOUT_SECONDS = 10.0
_MAX_BRIEF_CHARS = 1200
_JSON_BLOCK_PATTERN = re.compile(r"\{.*\}", re.DOTALL)
_HTML_TAG_PATTERN = re.compile(r"</?[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"[ \t]+")
_SOURCE_LABELS = {
    "meeting_notes": "会議メモ",
    "emails": "メール",
    "teams_chats": "Teams チャット",
    "documents_notes": "文書 / ノート",
}


class WorkIQContextResult(TypedDict):
    """Work IQ brief retrieval result."""

    brief_summary: str
    brief_source_metadata: list[WorkIQSourceMetadata]
    status: str
    warning_code: NotRequired[str]


def _sanitize_text(value: object) -> str:
    """レスポンス断片を軽量に正規化する。"""
    return str(value).strip() if value is not None else ""


def _failure_result(status: str) -> WorkIQContextResult:
    """fail-closed 用の結果を返す。"""
    return {
        "brief_summary": "",
        "brief_source_metadata": [],
        "status": status,
        "warning_code": status,
    }


def _resolve_timeout_seconds() -> float:
    """Work IQ 向け timeout を返す。"""
    settings = get_settings()
    raw_timeout = _sanitize_text(settings.get("work_iq_timeout_seconds"))
    try:
        timeout_seconds = float(raw_timeout)
    except ValueError:
        return _DEFAULT_TIMEOUT_SECONDS
    return timeout_seconds if timeout_seconds > 0 else _DEFAULT_TIMEOUT_SECONDS


def _build_headers(access_token: str) -> dict[str, str]:
    """Graph API 呼び出しヘッダーを構築する。"""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _build_brief_prompt(user_input: str, source_scope: list[str]) -> str:
    """職場コンテキスト要約用のプロンプトを組み立てる。"""
    scope_labels = [_SOURCE_LABELS.get(source, source) for source in source_scope if source]
    source_text = "、".join(scope_labels) if scope_labels else "会議メモ、メール、Teams チャット、文書 / ノート"
    return (
        "あなたは旅行マーケティング企画の準備メモを作るアシスタントです。"
        "Microsoft 365 の職場コンテキストだけを使い、次の依頼に関係する事実・制約・過去の議論だけを短く整理してください。\n\n"
        f"優先ソース: {source_text}\n"
        "要件:\n"
        "- Web 情報は使わない\n"
        "- 長い引用や原文転載は避け、要点だけを書く\n"
        "- 個人情報や機微情報は必要最小限にする\n"
        "- 関連情報が乏しい場合は brief_summary を空文字にする\n"
        '- 必ず JSON だけを返す: {"brief_summary":"...","key_points":["..."]}\n\n'
        f"ユーザー依頼:\n{user_input}"
    )


def _build_chat_payload(user_input: str, source_scope: list[str], user_time_zone: str) -> dict[str, Any]:
    """Graph Chat API の request body を構築する。"""
    return {
        "message": {
            "text": _build_brief_prompt(user_input, source_scope),
        },
        "locationHint": {
            "timeZone": _sanitize_text(user_time_zone) or "UTC",
        },
        "contextualResources": {
            "webContext": {
                "isWebEnabled": False,
            }
        },
    }


def _extract_assistant_message(payload: object) -> dict[str, Any]:
    """Graph Chat API 応答から assistant message を取り出す。"""
    if not isinstance(payload, dict):
        raise ValueError("invalid graph chat payload")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("graph chat payload did not include messages")
    for item in reversed(messages):
        if isinstance(item, dict) and _sanitize_text(item.get("text")):
            return item
    raise ValueError("assistant message was missing")


def _sanitize_brief_summary(text: str) -> str:
    """prompt 注入用の brief summary をコンパクトに整える。"""
    normalized = _HTML_TAG_PATTERN.sub("", text)
    normalized = normalized.replace("```json", "").replace("```", "")
    normalized = _WHITESPACE_PATTERN.sub(" ", normalized)
    return normalized.strip()[:_MAX_BRIEF_CHARS]


def _parse_brief_summary(response_text: str) -> str:
    """Chat API 応答から brief summary を抽出する。"""
    stripped = response_text.strip()
    if not stripped:
        return ""

    candidate_payloads = [stripped]
    match = _JSON_BLOCK_PATTERN.search(stripped)
    if match:
        candidate_payloads.append(match.group(0))

    for candidate in candidate_payloads:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        summary = _sanitize_brief_summary(
            _sanitize_text(
                parsed.get("brief_summary")
                or parsed.get("workplace_context_brief")
                or parsed.get("summary")
            )
        )
        if summary:
            return summary
        key_points = parsed.get("key_points") or parsed.get("highlights")
        if isinstance(key_points, list):
            bullet_summary = " / ".join(_sanitize_text(item) for item in key_points if _sanitize_text(item))
            if bullet_summary:
                return _sanitize_brief_summary(bullet_summary)

    return _sanitize_brief_summary(stripped)


def _classify_attribution_source(url: str, provider_name: str) -> str | None:
    """citation URL から大まかなソース種別を推定する。"""
    normalized_url = url.lower()
    normalized_provider = provider_name.lower()
    combined = f"{normalized_url} {normalized_provider}"

    if "teams.microsoft.com/l/meeting" in normalized_url or "meeting transcript" in combined or "meeting" in normalized_provider:
        return "meeting_notes"
    if "outlook" in combined or "mail" in combined or "email" in combined:
        return "emails"
    if "teams.microsoft.com" in normalized_url or "channel message" in combined or "chat" in combined:
        return "teams_chats"
    if "sharepoint.com" in normalized_url or "onedrive" in combined or any(
        extension in combined for extension in (".docx", ".pptx", ".xlsx", ".pdf", ".txt", ".md")
    ):
        return "documents_notes"
    return None


def _build_source_metadata(attributions: object, source_scope: list[str]) -> list[WorkIQSourceMetadata]:
    """Graph attributions から安全な source metadata を作る。"""
    if not isinstance(attributions, list):
        return []

    counts: Counter[str] = Counter()
    for item in attributions:
        if not isinstance(item, dict):
            continue
        source = _classify_attribution_source(
            _sanitize_text(item.get("seeMoreWebUrl")),
            _sanitize_text(item.get("providerDisplayName")),
        )
        if source and source in source_scope:
            counts[source] += 1

    metadata: list[WorkIQSourceMetadata] = []
    for source in source_scope:
        count = counts.get(source)
        if not count:
            continue
        item: WorkIQSourceMetadata = {"source": source, "count": count}
        label = _SOURCE_LABELS.get(source)
        if label:
            item["label"] = label
        metadata.append(item)
    return metadata


def _map_http_error(exc: httpx.HTTPStatusError) -> str:
    """HTTP エラーを UI 向け status へ写像する。"""
    status_code = exc.response.status_code
    body = exc.response.text.lower()

    if status_code == 401:
        return "auth_required"
    if status_code == 403:
        if "consent" in body or "permission" in body or "grant" in body:
            return "consent_required"
        if "license" in body or "copilot" in body:
            return "unavailable"
        return "consent_required"
    return "unavailable"


async def generate_workplace_context_brief(
    user_input: str,
    source_scope: list[str],
    access_token: str,
    user_time_zone: str = "UTC",
) -> WorkIQContextResult:
    """Microsoft Graph Copilot Chat API から職場コンテキスト brief を取得する。"""
    if not _sanitize_text(access_token):
        return _failure_result("auth_required")

    headers = _build_headers(access_token)
    timeout_seconds = _resolve_timeout_seconds()

    try:
        create_response = await get_http_client().post(
            _GRAPH_CONVERSATIONS_URL,
            json={},
            headers=headers,
            timeout=timeout_seconds,
        )
        create_response.raise_for_status()
        conversation_payload = create_response.json()
        conversation_id = (
            _sanitize_text(conversation_payload.get("id")) if isinstance(conversation_payload, dict) else ""
        )
        if not conversation_id:
            raise ValueError("graph conversation id was missing")

        chat_response = await get_http_client().post(
            f"{_GRAPH_CONVERSATIONS_URL}/{conversation_id}/chat",
            json=_build_chat_payload(user_input, source_scope, user_time_zone),
            headers=headers,
            timeout=timeout_seconds,
        )
        chat_response.raise_for_status()
        assistant_message = _extract_assistant_message(chat_response.json())
    except httpx.TimeoutException:
        return _failure_result("timeout")
    except httpx.HTTPStatusError as exc:
        logger.warning("work iq graph call failed: %s", exc)
        return _failure_result(_map_http_error(exc))
    except (httpx.HTTPError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("work iq graph call failed: %s", exc)
        return _failure_result("unavailable")

    brief_summary = _parse_brief_summary(_sanitize_text(assistant_message.get("text")))
    brief_source_metadata = _build_source_metadata(assistant_message.get("attributions"), source_scope)
    return {
        "brief_summary": brief_summary,
        "brief_source_metadata": brief_source_metadata,
        "status": "completed",
    }
