"""SSE / 成果物メタデータの additive schema と正規化ヘルパー。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, TypedDict
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

JsonScalar = str | int | float | bool | None
JsonObject = dict[str, JsonScalar]


class EvidenceItemPayload(TypedDict, total=False):
    """根拠ソースを UI / 評価へ渡すための最小 schema。"""

    id: str
    title: str
    source: str
    url: str
    quote: str
    relevance: float
    retrieved_at: str
    metadata: JsonObject


class ChartSpecPayload(TypedDict, total=False):
    """将来の Generative UI 向け chart schema。"""

    chart_type: str
    title: str
    x_label: str
    y_label: str
    series: list[str]
    data: list[JsonObject]
    metadata: JsonObject


class TraceEventPayload(TypedDict, total=False):
    """trace / span 互換の軽量イベント schema。"""

    event_id: str
    name: str
    phase: str
    status: str
    timestamp: str
    agent: str
    tool: str
    duration_ms: int
    metadata: JsonObject


class DebugEventPayload(TypedDict, total=False):
    """UI には既定表示しない debug event schema。"""

    event_id: str
    level: str
    message: str
    code: str
    timestamp: str
    agent: str
    metadata: JsonObject


class WorkIQSourceMetadataPayload(TypedDict, total=False):
    """Work IQ ソース概要の保存 / 表示 schema。"""

    source: str
    label: str
    count: int
    connector: str
    status: str
    confidence: float
    latest_timestamp: str
    evidence_ids: list[str]


class SourceIngestionStatePayload(TypedDict, total=False):
    """外部ソース取り込み状態の schema。"""

    source: str
    status: str
    run_id: str
    items_discovered: int
    items_ingested: int
    items_failed: int
    last_ingested_at: str
    error_code: str
    error_message: str


class PipelineMetricsPayload(TypedDict, total=False):
    """既存 metrics に追加できる拡張 schema。"""

    latency_seconds: float
    tool_calls: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    retry_count: int
    cache_hits: int
    cache_misses: int
    agent_latencies: dict[str, float]
    tool_latencies: dict[str, float]
    evidence: list[EvidenceItemPayload]
    charts: list[ChartSpecPayload]
    trace_events: list[TraceEventPayload]
    debug_events: list[DebugEventPayload]
    source_ingestion: list[SourceIngestionStatePayload]


class _SchemaModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


def _trimmed(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _safe_https_url(value: object) -> str | None:
    raw_value = _trimmed(value)
    if not raw_value:
        return None
    parsed = urlparse(raw_value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return raw_value


def _metadata_dict(value: object) -> JsonObject | None:
    if not isinstance(value, Mapping):
        return None
    metadata: JsonObject = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            continue
        if isinstance(item, str | int | float | bool) or item is None:
            metadata[key] = item
    return metadata or None


class EvidenceItem(_SchemaModel):
    id: str = ""
    title: str = ""
    source: str
    url: str | None = None
    quote: str | None = None
    relevance: float | None = Field(default=None, ge=0, le=1)
    retrieved_at: str | None = None
    metadata: JsonObject | None = None

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str | None:
        return _safe_https_url(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: object) -> JsonObject | None:
        return _metadata_dict(value)


class ChartSpec(_SchemaModel):
    chart_type: Literal["bar", "line", "area", "pie", "scatter", "table", "kpi", "mixed"] = "table"
    title: str = ""
    x_label: str | None = None
    y_label: str | None = None
    series: list[str] = Field(default_factory=list)
    data: list[JsonObject] = Field(default_factory=list)
    metadata: JsonObject | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: object) -> JsonObject | None:
        return _metadata_dict(value)


class TraceEvent(_SchemaModel):
    event_id: str = ""
    name: str
    phase: str | None = None
    status: str | None = None
    timestamp: str | None = None
    agent: str | None = None
    tool: str | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    metadata: JsonObject | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: object) -> JsonObject | None:
        return _metadata_dict(value)


class DebugEvent(_SchemaModel):
    event_id: str = ""
    level: Literal["debug", "info", "warning", "error"] = "debug"
    message: str
    code: str | None = None
    timestamp: str | None = None
    agent: str | None = None
    metadata: JsonObject | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def _normalize_metadata(cls, value: object) -> JsonObject | None:
        return _metadata_dict(value)


class WorkIQSourceMetadata(_SchemaModel):
    source: str
    label: str | None = None
    count: int | None = Field(default=None, ge=0)
    connector: str | None = None
    status: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    latest_timestamp: str | None = None
    evidence_ids: list[str] | None = None


class SourceIngestionState(_SchemaModel):
    source: str
    status: Literal["pending", "running", "completed", "partial", "failed", "skipped", "unknown"] = "unknown"
    run_id: str | None = None
    items_discovered: int | None = Field(default=None, ge=0)
    items_ingested: int | None = Field(default=None, ge=0)
    items_failed: int | None = Field(default=None, ge=0)
    last_ingested_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: object) -> str:
        normalized = _trimmed(value).lower()
        return normalized if normalized in {"pending", "running", "completed", "partial", "failed", "skipped"} else "unknown"

    @field_validator("items_discovered", "items_ingested", "items_failed", mode="before")
    @classmethod
    def _normalize_non_negative_int(cls, value: object) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None


class PipelineMetrics(_SchemaModel):
    latency_seconds: float = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    retry_count: int | None = Field(default=None, ge=0)
    cache_hits: int | None = Field(default=None, ge=0)
    cache_misses: int | None = Field(default=None, ge=0)
    agent_latencies: dict[str, float] | None = None
    tool_latencies: dict[str, float] | None = None
    evidence: list[EvidenceItem] | None = None
    charts: list[ChartSpec] | None = None
    trace_events: list[TraceEvent] | None = None
    debug_events: list[DebugEvent] | None = None
    source_ingestion: list[SourceIngestionState] | None = None

    @field_validator("agent_latencies", "tool_latencies", mode="before")
    @classmethod
    def _normalize_latency_map(cls, value: object) -> dict[str, float] | None:
        if not isinstance(value, Mapping):
            return None
        normalized: dict[str, float] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            try:
                parsed = float(item)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                normalized[key] = parsed
        return normalized or None


def _as_payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_none=True)


def _iter_candidate_mappings(value: object) -> Iterable[Mapping[str, object]]:
    if isinstance(value, Mapping):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                yield item


def _normalize_model_list(value: object, model_type: type[_SchemaModel]) -> list[dict]:
    normalized: list[dict] = []
    for item in _iter_candidate_mappings(value):
        try:
            normalized.append(_as_payload(model_type.model_validate(item)))
        except (TypeError, ValueError, ValidationError):
            continue
    return normalized


def normalize_evidence_items(value: object) -> list[EvidenceItemPayload]:
    """EvidenceItem 配列を安全な payload へ正規化する。"""
    return [EvidenceItemPayload(**item) for item in _normalize_model_list(value, EvidenceItem)]


def normalize_chart_specs(value: object) -> list[ChartSpecPayload]:
    """ChartSpec 配列を安全な payload へ正規化する。"""
    return [ChartSpecPayload(**item) for item in _normalize_model_list(value, ChartSpec)]


def normalize_trace_events(value: object) -> list[TraceEventPayload]:
    """trace event 配列を安全な payload へ正規化する。"""
    return [TraceEventPayload(**item) for item in _normalize_model_list(value, TraceEvent)]


def normalize_debug_events(value: object) -> list[DebugEventPayload]:
    """debug event 配列を安全な payload へ正規化する。"""
    return [DebugEventPayload(**item) for item in _normalize_model_list(value, DebugEvent)]


def normalize_work_iq_source_metadata(value: object) -> list[WorkIQSourceMetadataPayload]:
    """Work IQ source metadata 配列を安全な payload へ正規化する。"""
    return [WorkIQSourceMetadataPayload(**item) for item in _normalize_model_list(value, WorkIQSourceMetadata)]


def normalize_source_ingestion_state(value: object) -> list[SourceIngestionStatePayload]:
    """source ingestion state 配列を安全な payload へ正規化する。"""
    return [SourceIngestionStatePayload(**item) for item in _normalize_model_list(value, SourceIngestionState)]


def normalize_pipeline_metrics(value: object) -> PipelineMetricsPayload | None:
    """既存 metrics と拡張 metrics を後方互換に正規化する。"""
    if not isinstance(value, Mapping):
        return None
    try:
        normalized = PipelineMetrics.model_validate(value)
    except (TypeError, ValueError, ValidationError):
        return None
    return PipelineMetricsPayload(**_as_payload(normalized))
