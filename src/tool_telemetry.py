"""ツール実行テレメトリの共通ヘルパー。"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import TypedDict

from src.foundry_tracing import end_foundry_span, set_foundry_span_attributes, start_foundry_tool_span
from src.pipeline_schemas import (
    ChartSpecPayload,
    DebugEventPayload,
    EvidenceItemPayload,
    SourceIngestionStatePayload,
    TraceEventPayload,
    WorkIQSourceMetadataPayload,
    normalize_chart_specs,
    normalize_debug_events,
    normalize_evidence_items,
    normalize_source_ingestion_state,
    normalize_trace_events,
    normalize_work_iq_source_metadata,
)


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
    mcp_server_id: str
    auth_mode: str
    access_mode: str
    approval_policy: str
    approval_required: bool
    evidence: list[EvidenceItemPayload]
    charts: list[ChartSpecPayload]
    trace_events: list[TraceEventPayload]
    debug_events: list[DebugEventPayload]
    source_metadata: list[WorkIQSourceMetadataPayload]
    source_ingestion: list[SourceIngestionStatePayload]


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

_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "api-key",
    "authorization",
    "bearer",
    "client_secret",
    "code",
    "cookie",
    "key",
    "ocp-apim-subscription-key",
    "password",
    "secret",
    "sig",
    "subscription-key",
    "token",
    "x-functions-key",
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[-._~+/A-Za-z0-9=]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|client_secret|code|ocp-apim-subscription-key|password|secret|sig|token|x-functions-key)\b\s*[:=]\s*)(\"[^\"]+\"|'[^']+'|[^,\s&}]+)"
)
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|code|ocp-apim-subscription-key|secret|sig|subscription-key|token|x-functions-key)=)([^&#\s]+)"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tool_name(tool_name: str) -> str:
    """UI と backend で共通の canonical tool 名に揃える。"""
    normalized = tool_name.strip()
    return _TOOL_NAME_ALIASES.get(normalized, normalized)


def redact_sensitive_text(value: str) -> str:
    """テレメトリ文字列からトークン・キー・署名を除去する。"""
    redacted = _BEARER_PATTERN.sub(f"Bearer {_REDACTED}", value)
    redacted = _QUERY_SECRET_PATTERN.sub(rf"\1{_REDACTED}", redacted)
    return _ASSIGNMENT_PATTERN.sub(rf"\1{_REDACTED}", redacted)


def redact_sensitive_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    """テレメトリ payload の機密キーと文字列値を再帰的にマスクする。"""
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        normalized_key = key.strip().lower()
        if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS):
            redacted[key] = _REDACTED
            continue
        if isinstance(value, str):
            redacted[key] = redact_sensitive_text(value)
        elif isinstance(value, Mapping):
            redacted[key] = redact_sensitive_mapping(value)
        elif isinstance(value, list):
            redacted[key] = [
                redact_sensitive_mapping(item) if isinstance(item, Mapping) else redact_sensitive_text(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            redacted[key] = value
    return redacted


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
    evidence: Sequence[Mapping[str, object]] | None = None,
    charts: Sequence[Mapping[str, object]] | None = None,
    trace_events: Sequence[Mapping[str, object]] | None = None,
    debug_events: Sequence[Mapping[str, object]] | None = None,
    source_metadata: Sequence[Mapping[str, object]] | None = None,
    source_ingestion: Sequence[Mapping[str, object]] | None = None,
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
        payload["error_message"] = redact_sensitive_text(error_message)
    if source_scope:
        payload["source_scope"] = [item for item in source_scope if item]
    if evidence:
        normalized_evidence = normalize_evidence_items(list(evidence))
        if normalized_evidence:
            payload["evidence"] = normalized_evidence
    if charts:
        normalized_charts = normalize_chart_specs(list(charts))
        if normalized_charts:
            payload["charts"] = normalized_charts
    if trace_events:
        normalized_trace_events = normalize_trace_events(list(trace_events))
        if normalized_trace_events:
            payload["trace_events"] = normalized_trace_events
    if debug_events:
        normalized_debug_events = normalize_debug_events(list(debug_events))
        if normalized_debug_events:
            payload["debug_events"] = normalized_debug_events
    if source_metadata:
        normalized_source_metadata = normalize_work_iq_source_metadata(list(source_metadata))
        if normalized_source_metadata:
            payload["source_metadata"] = normalized_source_metadata
    if source_ingestion:
        normalized_source_ingestion = normalize_source_ingestion_state(list(source_ingestion))
        if normalized_source_ingestion:
            payload["source_ingestion"] = normalized_source_ingestion
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
    context = _context_var.get() or {}
    resolved_agent_name = (agent_name or str(context.get("agent_name") or "")).strip()
    resolved_step = step if step is not None else int(context.get("step") or 0)
    normalized_tool_name = normalize_tool_name(tool_name)
    resolved_source, resolved_provider = _resolve_provider(
        normalized_tool_name,
        source,
        provider or str(context.get("provider") or ""),
    )
    span = start_foundry_tool_span(
        tool_name=normalized_tool_name,
        agent_name=resolved_agent_name,
        step=resolved_step,
        source=resolved_source,
        provider=resolved_provider,
        source_scope=source_scope,
    )
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
        set_foundry_span_attributes(
            span,
            {
                "app.tool.duration_ms": duration_ms,
                "app.tool.success": False,
                "app.tool.error_code": exc.__class__.__name__,
            },
        )
        end_foundry_span(span, success=False, error_code=exc.__class__.__name__)
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
    set_foundry_span_attributes(span, {"app.tool.duration_ms": duration_ms, "app.tool.success": True})
    end_foundry_span(span, success=True)
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
