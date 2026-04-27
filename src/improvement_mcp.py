"""APIM 経由の MCP ツールを呼び出して改善ブリーフを取得する。"""

import ast
import itertools
import json
import logging
from typing import Any, TypedDict

import httpx

from src.config import get_settings
from src.http_client import get_http_client
from src.mcp_auth_registry import (
    McpServerRegistryEntry,
    build_improvement_mcp_registry_entry,
    build_mcp_auth_headers,
    decide_mcp_tool_policy,
    validate_mcp_registry_entry,
)

logger = logging.getLogger(__name__)

_MCP_PROTOCOL_VERSION = "2025-06-18"
_DEFAULT_API_KEY_HEADER = "Ocp-Apim-Subscription-Key"
_REQUEST_ID_COUNTER = itertools.count(1)


class PriorityIssue(TypedDict):
    """改善課題。"""

    label: str
    reason: str
    suggested_action: str


class ImprovementBriefResult(TypedDict):
    """MCP ツールから返る改善ブリーフ。"""

    evaluation_summary: str
    improvement_brief: str
    priority_issues: list[PriorityIssue]
    must_keep: list[str]


def is_improvement_mcp_configured() -> bool:
    """改善ブリーフ用 MCP endpoint が設定済みかを返す。"""
    settings = get_settings()
    return bool(settings["improvement_mcp_endpoint"].strip())


async def generate_improvement_brief(
    plan_markdown: str,
    evaluation_result: dict[str, Any] | None,
    regulation_summary: str,
    rejection_history: list[str],
    user_feedback: str,
) -> ImprovementBriefResult | None:
    """MCP サーバーから改善ブリーフを取得する。"""
    settings = get_settings()
    endpoint = settings["improvement_mcp_endpoint"].strip()
    if not endpoint:
        return None

    registry_entry = build_improvement_mcp_registry_entry(settings)
    if registry_entry is None:
        return None
    registry_errors = validate_mcp_registry_entry(registry_entry)
    if registry_errors:
        logger.warning("improvement MCP registry validation failed: %s", ", ".join(registry_errors))
        return None
    policy_decision = decide_mcp_tool_policy(registry_entry, "generate_improvement_brief")
    if not policy_decision.allowed:
        logger.warning("improvement MCP tool policy denied call: %s", policy_decision.reason)
        return None

    try:
        headers = _build_headers(settings, registry_entry)
    except ValueError as exc:
        logger.warning("improvement MCP auth configuration failed: %s", exc)
        return None

    session_id: str | None = None
    protocol_version = _MCP_PROTOCOL_VERSION

    try:
        session_id, protocol_version = await _initialize_session(endpoint, headers)
        result = await _call_tool(
            endpoint=endpoint,
            headers=headers,
            session_id=session_id,
            protocol_version=protocol_version,
            tool_name="generate_improvement_brief",
            arguments={
                "plan_markdown": plan_markdown,
                "evaluation_payload": json.dumps(evaluation_result or {}, ensure_ascii=False),
                "regulation_summary": regulation_summary,
                "rejection_history": json.dumps(rejection_history, ensure_ascii=False),
                "user_feedback": user_feedback,
            },
        )
        return _parse_tool_result(result)
    except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as exc:
        logger.warning("improvement MCP call failed: %s", exc)
        return None
    finally:
        if session_id:
            await _close_session(endpoint, headers, session_id, protocol_version)


def _build_headers(
    settings: dict[str, str],
    registry_entry: McpServerRegistryEntry | None = None,
) -> dict[str, str]:
    """MCP 呼び出し用ヘッダーを構築する。"""
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    registry = registry_entry or build_improvement_mcp_registry_entry(settings)
    if registry is not None:
        headers.update(
            build_mcp_auth_headers(
                registry.auth,
                secret_resolver=lambda secret_ref: _resolve_improvement_mcp_secret(settings, secret_ref),
            )
        )
    else:
        api_key = settings.get("improvement_mcp_api_key", "").strip()
        if api_key:
            header_name = settings.get("improvement_mcp_api_key_header", _DEFAULT_API_KEY_HEADER).strip()
            headers[header_name or _DEFAULT_API_KEY_HEADER] = api_key
    return headers


def _resolve_improvement_mcp_secret(settings: dict[str, str], secret_ref: str) -> str:
    """既存 env 設定から registry の secret reference を解決する。"""
    if secret_ref == "IMPROVEMENT_MCP_API_KEY":
        return settings.get("improvement_mcp_api_key", "")
    return ""


async def _initialize_session(endpoint: str, headers: dict[str, str]) -> tuple[str | None, str]:
    """MCP initialize/initialized ハンドシェイクを実行する。"""
    initialize_request_id = _next_request_id()
    initialize_response, session_id = await _post_jsonrpc(
        endpoint=endpoint,
        headers=headers,
        payload={
            "jsonrpc": "2.0",
            "id": initialize_request_id,
            "method": "initialize",
            "params": {
                "protocolVersion": _MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {
                    "name": "travel-marketing-fastapi",
                    "title": "Travel Marketing FastAPI",
                    "version": "1.0.0",
                },
            },
        },
    )
    if not initialize_response:
        raise ValueError("initialize response was empty")

    result = initialize_response.get("result")
    if not isinstance(result, dict):
        raise ValueError("initialize result was missing")

    protocol_version = str(result.get("protocolVersion") or _MCP_PROTOCOL_VERSION)
    await _post_jsonrpc(
        endpoint=endpoint,
        headers=headers,
        payload={"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        protocol_version=protocol_version,
    )
    return session_id, protocol_version


async def _call_tool(
    endpoint: str,
    headers: dict[str, str],
    session_id: str | None,
    protocol_version: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """tools/call を実行して結果を返す。"""
    request_id = _next_request_id()
    response_payload, _ = await _post_jsonrpc(
        endpoint=endpoint,
        headers=headers,
        session_id=session_id,
        protocol_version=protocol_version,
        payload={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    if not response_payload:
        raise ValueError("tools/call response was empty")
    if isinstance(response_payload.get("error"), dict):
        raise ValueError(str(response_payload["error"].get("message") or "MCP tool call failed"))

    result = response_payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("tools/call result was missing")
    return result


async def _post_jsonrpc(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    session_id: str | None = None,
    protocol_version: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """JSON-RPC payload を POST し、対応するレスポンスを返す。"""
    request_headers = dict(headers)
    if session_id:
        request_headers["Mcp-Session-Id"] = session_id
    if protocol_version:
        request_headers["MCP-Protocol-Version"] = protocol_version

    response = await get_http_client().post(endpoint, json=payload, headers=request_headers)
    response.raise_for_status()

    next_session_id = response.headers.get("Mcp-Session-Id") or session_id
    if response.status_code == 202 or not response.content:
        return None, next_session_id

    return _extract_jsonrpc_response(response, payload.get("id")), next_session_id


def _extract_jsonrpc_response(response: httpx.Response, request_id: object) -> dict[str, Any]:
    """HTTP レスポンスから対象の JSON-RPC メッセージを取り出す。"""
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type == "text/event-stream":
        messages = _parse_sse_messages(response.text)
    else:
        parsed_body = response.json()
        if isinstance(parsed_body, list):
            messages = [message for message in parsed_body if isinstance(message, dict)]
        elif isinstance(parsed_body, dict):
            messages = [parsed_body]
        else:
            raise ValueError("unexpected MCP response payload")

    for message in messages:
        if message.get("id") == request_id:
            return message
    for message in messages:
        if "result" in message or "error" in message:
            return message
    raise ValueError("JSON-RPC response message was not found")


def _parse_sse_messages(body: str) -> list[dict[str, Any]]:
    """SSE 形式のレスポンスから JSON メッセージを復元する。"""
    messages: list[dict[str, Any]] = []
    data_lines: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
        if line:
            continue
        if not data_lines:
            continue
        parsed = json.loads("\n".join(data_lines))
        if isinstance(parsed, dict):
            messages.append(parsed)
        data_lines.clear()

    if data_lines:
        parsed = json.loads("\n".join(data_lines))
        if isinstance(parsed, dict):
            messages.append(parsed)
    return messages


def _parse_tool_result(result: dict[str, Any]) -> ImprovementBriefResult:
    """tools/call result から改善ブリーフを正規化する。"""
    structured_content = result.get("structuredContent")
    if isinstance(structured_content, dict):
        return _coerce_improvement_brief(structured_content)

    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            parsed = _parse_content_payload(text)
            if isinstance(parsed, dict):
                return _coerce_improvement_brief(parsed)

    if isinstance(content, str) and content.strip():
        parsed = _parse_content_payload(content)
        if isinstance(parsed, dict):
            return _coerce_improvement_brief(parsed)

    raise ValueError("tool result did not contain an improvement brief")


def _parse_content_payload(content: str) -> dict[str, Any] | None:
    """MCP content.text の文字列表現を dict へ復元する。"""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(content)
        except SyntaxError, ValueError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _coerce_improvement_brief(payload: dict[str, Any]) -> ImprovementBriefResult:
    """任意 dict を ImprovementBriefResult へ寄せる。"""
    priority_issues: list[PriorityIssue] = []
    raw_priority_issues = payload.get("priority_issues")
    if isinstance(raw_priority_issues, list):
        for item in raw_priority_issues:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")
            reason = str(item.get("reason") or "")
            suggested_action = str(item.get("suggested_action") or "")
            if not label or not suggested_action:
                continue
            priority_issues.append(
                {
                    "label": label,
                    "reason": reason,
                    "suggested_action": suggested_action,
                }
            )

    must_keep: list[str] = []
    raw_must_keep = payload.get("must_keep")
    if isinstance(raw_must_keep, list):
        for item in raw_must_keep:
            if not isinstance(item, str):
                continue
            stripped = item.strip()
            if stripped:
                must_keep.append(stripped)

    return {
        "evaluation_summary": str(payload.get("evaluation_summary") or ""),
        "improvement_brief": str(payload.get("improvement_brief") or ""),
        "priority_issues": priority_issues,
        "must_keep": must_keep,
    }


async def _close_session(
    endpoint: str,
    headers: dict[str, str],
    session_id: str,
    protocol_version: str,
) -> None:
    """必要ならサーバー側セッションを明示的に閉じる。"""
    request_headers = dict(headers)
    request_headers["Mcp-Session-Id"] = session_id
    request_headers["MCP-Protocol-Version"] = protocol_version

    try:
        response = await get_http_client().delete(endpoint, headers=request_headers)
    except httpx.HTTPError as exc:
        logger.debug("failed to close MCP session: %s", exc)
        return

    if response.status_code not in {200, 202, 204, 404, 405}:
        logger.debug("unexpected MCP session close status: %s", response.status_code)


def _next_request_id() -> str:
    """JSON-RPC request id を生成する。"""
    return str(next(_REQUEST_ID_COUNTER))
