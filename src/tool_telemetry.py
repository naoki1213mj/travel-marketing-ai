"""ツール実行テレメトリの共通ヘルパー。"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import TypedDict


class ToolEventPayload(TypedDict, total=False):
    """SSE で流す tool_event payload。"""

    event_id: str
    tool: str
    status: str
    agent: str
    step: int
    step_key: str
    source: str
    provider: str
    display_name: str
    version: int
    round: int
    phase: str
    fallback: str
    inferred: bool
    background_update: bool
    started_at: str
    finished_at: str
    duration_ms: int
    error_code: str
    error_message: str
    source_scope: list[str]


ToolEventCollector = Callable[[ToolEventPayload], None]

_collector_var: ContextVar[ToolEventCollector | None] = ContextVar("tool_event_collector", default=None)
_context_var: ContextVar[dict[str, object] | None] = ContextVar("tool_event_context", default=None)

_TOOL_NAME_ALIASES: dict[str, str] = {
    "search_knowledge_base": "foundry_iq_search",
}

_AGENT_STEP_KEYS: dict[str, str] = {
    "data-search-agent": "data-search-agent",
    "marketing-plan-agent": "marketing-plan-agent",
    "regulation-check-agent": "regulation-check-agent",
    "plan-revision-agent": "regulation-check-agent",
    "brochure-gen-agent": "brochure-gen-agent",
    "video-gen-agent": "video-gen-agent",
    "quality-review-agent": "quality-review-agent",
    "improvement-mcp": "marketing-plan-agent",
}

_TOOL_PROVIDERS: dict[str, tuple[str, str]] = {
    "generate_workplace_context_brief": ("workiq", "workiq"),
    "generate_improvement_brief": ("mcp", "mcp"),
    "web_search": ("foundry", "foundry"),
    "code_interpreter": ("foundry", "foundry"),
    "foundry_iq_search": ("foundry", "foundry"),
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tool_name(tool_name: str) -> str:
    """UI と backend で共通の canonical tool 名に揃える。"""
    normalized = tool_name.strip()
    return _TOOL_NAME_ALIASES.get(normalized, normalized)


def resolve_step_key(agent_name: str) -> str:
    """agent 名から workflow step key を解決する。"""
    normalized = agent_name.strip()
    return _AGENT_STEP_KEYS.get(normalized, normalized)


def _resolve_provider(
    tool_name: str,
    source: str | None,
    provider: str | None,
) -> tuple[str | None, str | None]:
    if provider:
        resolved_provider = provider
    elif source:
        resolved_provider = source
    else:
        resolved_provider = _TOOL_PROVIDERS.get(tool_name, (None, None))[0]

    if source:
        resolved_source = source
    else:
        resolved_source = _TOOL_PROVIDERS.get(tool_name, (None, None))[1] if resolved_provider else None

    if resolved_provider == "local" and resolved_source is None:
        resolved_source = "local"
    return resolved_source, resolved_provider


def build_tool_event_data(
    tool_name: str,
    status: str,
    *,
    agent_name: str | None = None,
    step: int | None = None,
    step_key: str | None = None,
    source: str | None = None,
    provider: str | None = None,
    display_name: str | None = None,
    version: int | None = None,
    round_number: int | None = None,
    phase: str | None = None,
    fallback: str | None = None,
    inferred: bool = False,
    background_update: bool = False,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    source_scope: Sequence[str] | None = None,
) -> ToolEventPayload:
    """tool_event payload を canonical schema に整形する。"""
    context = _context_var.get() or {}
    normalized_tool = normalize_tool_name(tool_name)
    resolved_agent = (agent_name or str(context.get("agent_name") or "")).strip()
    resolved_step_key = (step_key or str(context.get("step_key") or "")).strip() or resolve_step_key(resolved_agent)
    resolved_step = step if step is not None else int(context.get("step") or 0)
    resolved_version = version if version is not None else int(context.get("version") or 0)
    resolved_round = round_number if round_number is not None else int(context.get("round_number") or 0)
    resolved_phase = (phase or str(context.get("phase") or "tool")).strip()
    resolved_source, resolved_provider = _resolve_provider(normalized_tool, source, provider or str(context.get("provider") or ""))

    payload: ToolEventPayload = {
        "event_id": uuid.uuid4().hex,
        "tool": normalized_tool,
        "status": status.strip(),
        "agent": resolved_agent,
        "step_key": resolved_step_key,
        "phase": resolved_phase or "tool",
    }

    if resolved_step > 0:
        payload["step"] = resolved_step
    if resolved_source:
        payload["source"] = resolved_source
    if resolved_provider:
        payload["provider"] = resolved_provider
    if display_name:
        payload["display_name"] = display_name
    if resolved_version > 0:
        payload["version"] = resolved_version
    if resolved_round > 0:
        payload["round"] = resolved_round
    if fallback:
        payload["fallback"] = fallback
    if inferred:
        payload["inferred"] = True
    if background_update:
        payload["background_update"] = True
    if started_at:
        payload["started_at"] = started_at
    if finished_at:
        payload["finished_at"] = finished_at
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if error_code:
        payload["error_code"] = error_code
    if error_message:
        payload["error_message"] = error_message
    if source_scope:
        payload["source_scope"] = [item for item in source_scope if item]
    return payload


def emit_tool_event(payload: ToolEventPayload) -> ToolEventPayload:
    """現在の collector に tool_event を流す。collector がなくても payload は返す。"""
    collector = _collector_var.get()
    if collector is not None:
        collector(payload)
    return payload


@contextmanager
def tool_event_context(
    collector: ToolEventCollector | None,
    *,
    agent_name: str,
    step: int,
    step_key: str | None = None,
    version: int | None = None,
    round_number: int | None = None,
    provider: str | None = None,
    phase: str = "tool",
):
    """tool 実行中の contextvars を設定する。"""
    collector_token: Token[ToolEventCollector | None] = _collector_var.set(collector)
    context_token: Token[dict[str, object] | None] = _context_var.set(
        {
            "agent_name": agent_name,
            "step": step,
            "step_key": step_key or resolve_step_key(agent_name),
            "version": version or 0,
            "round_number": round_number or 0,
            "provider": provider or "",
            "phase": phase,
        }
    )
    try:
        yield
    finally:
        _context_var.reset(context_token)
        _collector_var.reset(collector_token)


@asynccontextmanager
async def trace_tool_invocation(
    tool_name: str,
    *,
    agent_name: str | None = None,
    step: int | None = None,
    step_key: str | None = None,
    source: str | None = None,
    provider: str | None = "local",
    display_name: str | None = None,
    source_scope: Sequence[str] | None = None,
):
    """local tool 実行の開始・完了・失敗を自動記録する。"""
    started_at = _utc_now_iso()
    started_perf = time.perf_counter()
    emit_tool_event(
        build_tool_event_data(
            tool_name,
            "running",
            agent_name=agent_name,
            step=step,
            step_key=step_key,
            source=source,
            provider=provider,
            display_name=display_name,
            started_at=started_at,
            source_scope=source_scope,
        )
    )
    try:
        yield
    except Exception as exc:
        duration_ms = max(int((time.perf_counter() - started_perf) * 1000), 0)
        emit_tool_event(
            build_tool_event_data(
                tool_name,
                "failed",
                agent_name=agent_name,
                step=step,
                step_key=step_key,
                source=source,
                provider=provider,
                display_name=display_name,
                started_at=started_at,
                finished_at=_utc_now_iso(),
                duration_ms=duration_ms,
                error_code=exc.__class__.__name__,
                error_message=str(exc)[:500],
                source_scope=source_scope,
            )
        )
        raise

    duration_ms = max(int((time.perf_counter() - started_perf) * 1000), 0)
    emit_tool_event(
        build_tool_event_data(
            tool_name,
            "completed",
            agent_name=agent_name,
            step=step,
            step_key=step_key,
            source=source,
            provider=provider,
            display_name=display_name,
            started_at=started_at,
            finished_at=_utc_now_iso(),
            duration_ms=duration_ms,
            source_scope=source_scope,
        )
    )
