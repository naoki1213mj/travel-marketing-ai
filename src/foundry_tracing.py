"""Foundry / OpenAI 向けの privacy-safe OpenTelemetry helper。"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from typing import Any, TypedDict

from src.config import AppSettings, get_settings


class AppInsightsAssociationStatus(TypedDict):
    """Application Insights へ安全に関連付けられるかを表す。"""

    configured: bool
    associated: bool
    reason: str


SpanAttributeValue = str | bool | int | float | Sequence[str | bool | int | float]

_TRUE_VALUES = {"1", "true", "yes", "y", "on", "enabled"}
_SENSITIVE_ATTRIBUTE_PARTS = (
    "authorization",
    "bearer",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "subscription_key",
    "prompt",
    "content",
    "html",
    "transcript",
    "raw",
    "email",
    "upn",
    "header",
)
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_SAFE_NAME_PART_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _parse_bool_setting(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def _has_value(value: str | None) -> bool:
    return bool((value or "").strip())


def get_app_insights_association_status(settings: AppSettings | None = None) -> AppInsightsAssociationStatus:
    """App Insights connection string が関連付け可能かを機密値なしで判定する。"""
    resolved = settings or get_settings()
    connection_string = resolved["applicationinsights_connection_string"].strip()
    if not connection_string:
        return {"configured": False, "associated": False, "reason": "missing_connection_string"}

    parts = {
        key.strip().lower(): value.strip()
        for segment in connection_string.split(";")
        if "=" in segment
        for key, value in [segment.split("=", 1)]
    }
    has_instrumentation_key = _has_value(parts.get("instrumentationkey"))
    has_application_id = _has_value(parts.get("applicationid"))
    if not has_instrumentation_key and not has_application_id:
        return {"configured": True, "associated": False, "reason": "missing_app_insights_identifier"}
    return {"configured": True, "associated": True, "reason": "associated"}


def is_foundry_tracing_enabled(settings: AppSettings | None = None) -> bool:
    """Foundry/OpenAI span を送信してよい状態かを返す。既定は false。"""
    resolved = settings or get_settings()
    if not _parse_bool_setting(resolved["enable_foundry_tracing"]):
        return False
    if not _has_value(resolved["project_endpoint"]):
        return False
    return get_app_insights_association_status(resolved)["associated"]


def hash_identifier(value: str | None) -> str:
    """会話 ID 等を raw 値ではなく短い hash にする。"""
    normalized = (value or "").strip()
    if not normalized:
        return "unknown"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _redacted_hash(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"[redacted:{digest}]"


def _attribute_name_is_sensitive(name: str) -> bool:
    normalized = name.lower().replace("-", "_").replace(".", "_")
    return any(part in normalized for part in _SENSITIVE_ATTRIBUTE_PARTS)


def _string_value_is_sensitive(value: str) -> bool:
    lowered = value.lower()
    return (
        value.startswith("http://")
        or value.startswith("https://")
        or value.startswith("data:")
        or "bearer " in lowered
        or "authorization:" in lowered
        or "instrumentationkey=" in lowered
        or "ocp-apim-subscription-key" in lowered
        or "-----begin " in lowered
        or "<html" in lowered
        or "<!doctype" in lowered
        or "<body" in lowered
        or _EMAIL_RE.match(value) is not None
        or _JWT_RE.match(value) is not None
    )


def redact_span_attribute_value(name: str, value: Any) -> SpanAttributeValue:
    """OpenTelemetry 属性値から秘密・本文・長文を除外する。"""
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        cleaned = _CONTROL_CHAR_RE.sub("", value).strip()
        if not cleaned:
            return "unknown"
        if _attribute_name_is_sensitive(name) or _string_value_is_sensitive(cleaned):
            return _redacted_hash(cleaned)
        if len(cleaned) > 160:
            return _redacted_hash(cleaned)
        return cleaned
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray | str):
        return [
            redact_span_attribute_value(f"{name}.item", item)
            for item in value
            if isinstance(item, str | bool | int | float)
        ][:20]
    return str(value.__class__.__name__)


def sanitize_span_attributes(attributes: Mapping[str, Any]) -> dict[str, SpanAttributeValue]:
    """None を落とし、値を redaction 済みにした span 属性を返す。"""
    return {key: redact_span_attribute_value(key, value) for key, value in attributes.items() if value is not None}


def safe_span_name_part(value: str | None) -> str:
    """span 名に使える限定文字へ正規化する。"""
    normalized = _SAFE_NAME_PART_RE.sub("-", (value or "").strip())[:80].strip("-")
    return normalized or "unknown"


def resolve_model_deployment(model_settings: Mapping[str, object] | None, settings: AppSettings | None = None) -> str:
    """リクエスト設定または環境設定からモデル deployment 名を安全に解決する。"""
    if model_settings and isinstance(model_settings.get("model"), str) and str(model_settings["model"]).strip():
        return str(model_settings["model"]).strip()
    resolved = settings or get_settings()
    return resolved["model_name"].strip() or "unknown"


def _get_tracer() -> Any | None:
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer("travel-marketing-agents.foundry")


def start_foundry_agent_span(
    *,
    agent_name: str,
    conversation_id: str,
    step: int,
    model_deployment: str,
    work_iq_enabled: bool,
    work_iq_status: str,
    settings: AppSettings | None = None,
) -> Any | None:
    """エージェント実行 span を開始する。無効時は None。"""
    resolved = settings or get_settings()
    if not is_foundry_tracing_enabled(resolved):
        return None
    tracer = _get_tracer()
    if tracer is None:
        return None
    return tracer.start_span(
        f"foundry.agent.{safe_span_name_part(agent_name)}",
        attributes=sanitize_span_attributes(
            {
                "gen_ai.system": "azure.openai",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": model_deployment,
                "app.conversation.hash": hash_identifier(conversation_id),
                "app.agent.name": agent_name,
                "app.agent.step": step,
                "app.work_iq.enabled": work_iq_enabled,
                "app.work_iq.status": work_iq_status or "disabled",
                "app.telemetry.app_insights.associated": True,
            }
        ),
    )


def start_foundry_tool_span(
    *,
    tool_name: str,
    agent_name: str,
    step: int,
    source: str | None,
    provider: str | None,
    source_scope: Sequence[str] | None = None,
    settings: AppSettings | None = None,
) -> Any | None:
    """ツール実行 span を開始する。引数・戻り値本文は記録しない。"""
    resolved = settings or get_settings()
    if not is_foundry_tracing_enabled(resolved):
        return None
    tracer = _get_tracer()
    if tracer is None:
        return None
    return tracer.start_span(
        f"foundry.tool.{safe_span_name_part(tool_name)}",
        attributes=sanitize_span_attributes(
            {
                "gen_ai.system": "azure.openai",
                "gen_ai.operation.name": "tool_call",
                "gen_ai.tool.name": tool_name,
                "app.agent.name": agent_name,
                "app.agent.step": step,
                "app.tool.source": source,
                "app.tool.provider": provider,
                "app.work_iq.source_scope": list(source_scope or []),
            }
        ),
    )


def set_foundry_span_attributes(span: Any | None, attributes: Mapping[str, Any]) -> None:
    """span 属性を redaction 済みで設定する。"""
    if span is None:
        return
    try:
        for key, value in sanitize_span_attributes(attributes).items():
            span.set_attribute(key, value)
    except (RuntimeError, TypeError, ValueError):
        return


def end_foundry_span(span: Any | None, *, success: bool, error_code: str | None = None) -> None:
    """span を安全に終了する。エラー詳細本文は記録しない。"""
    if span is None:
        return
    try:
        if error_code:
            span.set_attribute("error.type", redact_span_attribute_value("error.type", error_code))
        try:
            from opentelemetry.trace import Status, StatusCode

            span.set_status(Status(StatusCode.OK if success else StatusCode.ERROR, error_code or None))
        except ImportError:
            pass
    except (RuntimeError, TypeError, ValueError):
        pass
    finally:
        try:
            span.end()
        except RuntimeError:
            pass
