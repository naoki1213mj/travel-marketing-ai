"""品質評価 API。Built-in + カスタム評価器でパイプライン成果物を評価する。"""

import json
import logging
import os
import re
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import AppSettings, get_settings
from src.continuous_monitoring import build_evaluation_monitoring_record, schedule_continuous_monitoring
from src.conversations import append_conversation_events, get_conversation
from src.model_deployments import parse_bool_setting
from src.pipeline_schemas import (
    ChartSpecPayload,
    EvidenceItemPayload,
    normalize_chart_specs,
    normalize_evidence_items,
)
from src.request_identity import RequestIdentityError, extract_request_identity

router = APIRouter(prefix="/api", tags=["evaluation"])
logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

_PLAN_BUILTIN_METRICS = ("relevance", "coherence", "fluency")
_MARKETING_METRICS = ("appeal", "differentiation", "kpi_validity", "brand_tone")
_PLAN_CUSTOM_METRICS = (
    "plan_structure_readiness",
    "target_fit_readiness",
    "kpi_evidence_readiness",
    "offer_specificity",
    "travel_law_compliance",
)
_LEGACY_PLAN_CUSTOM_METRIC_ALIASES = {
    "senior_fit_readiness": "target_fit_readiness",
}
_ASSET_CUSTOM_METRICS = (
    "cta_visibility",
    "value_visibility",
    "trust_signal_presence",
    "disclosure_completeness",
    "accessibility_readiness",
)
_MAX_EVAL_QUERY_CHARS = 4000
_MAX_EVAL_RESPONSE_CHARS = 12000
_EVALUATION_LOG_SCHEMA_VERSION = "2026-04-privacy-v1"
_DEFAULT_EVALUATION_LOG_RETENTION_DAYS = 30
_SENSITIVE_LOG_PATTERN = re.compile(
    r"(?i)(bearer\s+[a-z0-9._~+/=-]+|authorization\s*[:=]\s*[^,\s]+|api[_-]?key\s*[:=]\s*[^,\s]+|token\s*[:=]\s*[^,\s]+|<[^>]+>)"
)
_METRIC_LABELS = {
    "relevance": "依頼適合性",
    "coherence": "構成の一貫性",
    "fluency": "表現の明瞭さ",
    "appeal": "顧客訴求力",
    "differentiation": "差別化",
    "kpi_validity": "KPI 妥当性",
    "brand_tone": "ブランド一貫性",
    "plan_structure_readiness": "企画書構成の完成度",
    "target_fit_readiness": "ターゲット適合性",
    "senior_fit_readiness": "ターゲット適合性",
    "kpi_evidence_readiness": "KPI 根拠の明確さ",
    "offer_specificity": "募集条件の具体性",
    "travel_law_compliance": "旅行業法準備度",
    "cta_visibility": "予約導線の明確さ",
    "value_visibility": "オファー訴求の明確さ",
    "trust_signal_presence": "安心材料の見えやすさ",
    "disclosure_completeness": "表示事項の網羅性",
    "accessibility_readiness": "アクセシビリティ準備度",
    "source_coverage": "根拠ソース網羅性",
    "chart_support": "チャート根拠",
    "finding_linkage": "指摘と根拠の紐づき",
    "citation_safety": "安全な根拠表示",
}


class EvaluateRequest(BaseModel):
    """評価リクエスト"""

    query: str = Field(..., description="ユーザーの指示テキスト")
    response: str = Field(..., description="企画書の Markdown テキスト")
    html: str = Field("", description="ブローシャの HTML テキスト（オプション）")
    conversation_id: str | None = Field(default=None, description="保存先の会話ID")
    artifact_version: int | None = Field(default=None, ge=1, description="評価対象の成果物バージョン")
    evidence: list[dict[str, object]] = Field(default_factory=list, description="評価に使う根拠ソース")
    charts: list[dict[str, object]] = Field(default_factory=list, description="評価に使うチャート仕様")


def _truncate_for_evaluation(text: str, limit: int) -> str:
    """評価器へ渡すテキスト長を制限する。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n\n[truncated]"


def _average(values: list[float]) -> float:
    """有効な数値の平均を返す。"""
    valid = [value for value in values if value >= 0]
    if not valid:
        return -1.0
    return round(sum(valid) / len(valid), 2)


def _count_matches(details: dict[str, bool]) -> tuple[int, int]:
    """checklist の一致件数を返す。"""
    total = len(details)
    passed = sum(1 for value in details.values() if value)
    return passed, total


def _build_check_metric(details: dict[str, bool], unavailable_reason: str | None = None) -> dict:
    """bool checklist を評価結果 dict へ変換する。"""
    if unavailable_reason is not None:
        return {
            "score": -1.0,
            "reason": unavailable_reason,
        }

    passed, total = _count_matches(details)
    score = round(passed / total, 2) if total else -1.0
    return {
        "score": score,
        "details": details,
        "reason": f"{total} 項目中 {passed} 項目を満たしています",
    }


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    """キーワード群のいずれかが含まれているかを返す。"""
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


_TARGET_SEGMENT_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "family": (
        ("家族", "ファミリー", "親子", "子連れ", "三世代"),
        ("家族", "ファミリー", "親子", "子ども", "お子さま", "三世代", "夏休み", "安心"),
    ),
    "senior": (
        ("シニア", "60代", "70代", "高齢", "熟年"),
        ("シニア", "ゆったり", "無理のない", "添乗", "サポート", "バリアフリー", "休憩"),
    ),
    "couple": (
        ("カップル", "夫婦", "記念日", "ハネムーン", "新婚"),
        ("カップル", "夫婦", "記念日", "二人", "ロマンチック", "夜景", "特別な時間"),
    ),
    "youth": (
        ("学生", "若者", "20代", "30代", "女子旅", "Z世代"),
        ("学生", "若者", "女子旅", "SNS", "映え", "アクティブ", "気軽"),
    ),
    "solo": (
        ("一人旅", "ひとり旅", "ソロ"),
        ("一人旅", "ひとり", "自由行動", "気まま", "自分時間", "気軽"),
    ),
    "luxury": (
        ("高級", "ラグジュアリー", "富裕層", "プレミアム", "ハイエンド"),
        ("高級", "ラグジュアリー", "上質", "プレミアム", "特別", "限定", "専用"),
    ),
    "inbound": (
        ("訪日", "インバウンド", "海外", "外国人"),
        ("訪日", "インバウンド", "多言語", "英語", "外国人", "Wi-Fi", "送迎"),
    ),
}


def _detect_target_segment(text: str) -> str | None:
    """依頼テキストから主要ターゲットセグメントを推定する。"""
    for segment, (query_keywords, _) in _TARGET_SEGMENT_HINTS.items():
        if _contains_any(text, query_keywords):
            return segment
    return None


def _matches_target_segment(segment: str | None, response: str) -> bool:
    """企画書が依頼ターゲットに沿った訴求を含むかを判定する。"""
    if segment is None:
        return _contains_any(
            response,
            (
                "ターゲット",
                "ペルソナ",
                "向け",
                "家族",
                "カップル",
                "夫婦",
                "学生",
                "一人旅",
                "女子旅",
                "訪日",
                "シニア",
            ),
        )

    _, response_keywords = _TARGET_SEGMENT_HINTS[segment]
    return _contains_any(response, response_keywords)


def _normalize_metric_score(score: float) -> float:
    """0-1/1-5 のスコアを 1-5 表示へ正規化する。"""
    if score < 0:
        return -1.0
    if score <= 1:
        return round(score * 5, 2)
    return round(score, 2)


def _clone_metric(metric: dict, key: str) -> dict:
    """UI 用ラベル付き metric を返す。"""
    cloned = dict(metric)
    score = cloned.get("score")
    if isinstance(score, (int, float)):
        cloned["score"] = _normalize_metric_score(float(score))
    cloned["label"] = _METRIC_LABELS.get(key, key)
    return cloned


def _build_quality_summary(metrics: dict[str, dict], stable_message: str) -> tuple[str, list[str]]:
    """quality category の summary と focus areas を返す。"""
    valid_items = [
        (key, metric)
        for key, metric in metrics.items()
        if isinstance(metric.get("score"), (int, float)) and float(metric["score"]) >= 0
    ]
    if not valid_items:
        return "評価対象がまだ揃っていません。", []

    ranked = sorted(valid_items, key=lambda item: float(item[1]["score"]))
    focus_areas = [metric.get("label", key) for key, metric in ranked if float(metric["score"]) < 4.0][:3]
    if not focus_areas:
        return stable_message, []

    labels = "、".join(str(label) for label in focus_areas)
    return f"優先補強ポイント: {labels}", [str(label) for label in focus_areas]


def _build_quality_category(metrics: dict[str, dict], stable_message: str) -> dict:
    """評価カテゴリを構築する。"""
    overall = _average(
        [
            float(metric["score"])
            for metric in metrics.values()
            if isinstance(metric.get("score"), (int, float)) and float(metric["score"]) >= 0
        ]
    )
    summary, focus_areas = _build_quality_summary(metrics, stable_message)
    return {
        "overall": overall,
        "summary": summary,
        "focus_areas": focus_areas,
        "metrics": metrics,
    }


def _build_legacy_conversion_metric(asset_metrics: dict[str, dict]) -> dict:
    """旧 conversion_potential 互換の集約 metric を返す。"""
    details: dict[str, bool] = {}
    for metric in asset_metrics.values():
        metric_details = metric.get("details")
        if isinstance(metric_details, dict):
            details.update({key: bool(value) for key, value in metric_details.items()})

    if not details:
        return {
            "score": -1.0,
            "reason": "ブローシャ HTML が未生成のため未評価です",
        }

    return _build_check_metric(details)


def _dedupe_evidence_items(evidence: list[EvidenceItemPayload]) -> list[EvidenceItemPayload]:
    """根拠 item を安定した key で重複除外し、空 id を補完する。"""
    deduped: list[EvidenceItemPayload] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in evidence:
        key = (
            str(item.get("id", "")),
            str(item.get("source", "")),
            str(item.get("title", "")),
            str(item.get("url", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        normalized = EvidenceItemPayload(**item)
        if not normalized.get("id"):
            normalized["id"] = f"eval-ev-{len(deduped) + 1}"
        deduped.append(normalized)
    return deduped


def _dedupe_chart_specs(charts: list[ChartSpecPayload]) -> list[ChartSpecPayload]:
    """chart spec を重複除外する。"""
    deduped: list[ChartSpecPayload] = []
    seen: set[tuple[str, str]] = set()
    for chart in charts:
        key = (str(chart.get("chart_type", "")), str(chart.get("title", "")))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chart)
    return deduped


def _normalize_evaluation_context(
    evidence: object,
    charts: object,
) -> tuple[list[EvidenceItemPayload], list[ChartSpecPayload]]:
    """評価 panel 用の evidence / charts を保存済み schema へ正規化する。"""
    return _dedupe_evidence_items(normalize_evidence_items(evidence)), _dedupe_chart_specs(normalize_chart_specs(charts))


def _append_context_from_data(
    data: Mapping[str, object],
    evidence: list[EvidenceItemPayload],
    charts: list[ChartSpecPayload],
) -> None:
    """SSE data payload から evidence / charts を抽出する。"""
    if "evidence" in data:
        evidence.extend(normalize_evidence_items(data.get("evidence")))
    if "charts" in data:
        charts.extend(normalize_chart_specs(data.get("charts")))

    metrics = data.get("metrics")
    if isinstance(metrics, Mapping):
        if "evidence" in metrics:
            evidence.extend(normalize_evidence_items(metrics.get("evidence")))
        if "charts" in metrics:
            charts.extend(normalize_chart_specs(metrics.get("charts")))


def _restore_evaluation_context_for_version(
    conversation: dict | None,
    artifact_version: int,
) -> tuple[list[EvidenceItemPayload], list[ChartSpecPayload]]:
    """会話履歴から指定 version の evidence / charts を復元する。"""
    if not isinstance(conversation, dict):
        return [], []

    evidence: list[EvidenceItemPayload] = []
    charts: list[ChartSpecPayload] = []
    current_version = 1
    target_version = max(1, artifact_version)

    for event in conversation.get("messages", []):
        if not isinstance(event, dict):
            continue
        data = event.get("data")
        if current_version == target_version and isinstance(data, Mapping):
            _append_context_from_data(data, evidence, charts)

        if event.get("event") == "done":
            if current_version >= target_version:
                break
            current_version += 1

    return _dedupe_evidence_items(evidence), _dedupe_chart_specs(charts)


def _evidence_ids_for_sources(
    evidence: list[EvidenceItemPayload],
    source_hints: tuple[str, ...],
    limit: int = 4,
) -> list[str]:
    """source hint に一致する evidence id を返す。"""
    matched: list[str] = []
    normalized_hints = tuple(hint.lower() for hint in source_hints)
    for item in evidence:
        source = str(item.get("source", "")).lower()
        if normalized_hints and not any(hint in source for hint in normalized_hints):
            continue
        evidence_id = str(item.get("id", "")).strip()
        if evidence_id and evidence_id not in matched:
            matched.append(evidence_id)
        if len(matched) >= limit:
            break
    if matched:
        return matched
    return [str(item.get("id", "")).strip() for item in evidence[:limit] if str(item.get("id", "")).strip()]


def _unit_score(metric: dict | None) -> float:
    """0-1 または 1-5 metric を 0-1 に丸める。"""
    if not isinstance(metric, dict) or not isinstance(metric.get("score"), (int, float)):
        return -1.0
    score = float(metric.get("score", -1))
    if score < 0:
        return -1.0
    return min(1.0, score / 5 if score > 1 else score)


def _finding_status(score: float) -> str:
    """unit score を finding status へ変換する。"""
    if score < 0:
        return "na"
    if score >= 0.8:
        return "pass"
    if score >= 0.55:
        return "warn"
    return "fail"


def _finding_confidence(score: float, evidence_ids: list[str]) -> float:
    """根拠量と metric score から confidence を決める。"""
    if score < 0:
        return 0.2
    base = 0.52 + min(len(evidence_ids), 3) * 0.1
    return round(min(0.95, base + score * 0.18), 2)


def _build_evaluation_findings(
    plan_metrics: dict[str, dict],
    asset_metrics: dict[str, dict],
    evidence: list[EvidenceItemPayload],
    charts: list[ChartSpecPayload],
) -> list[dict]:
    """評価結果から UI 用 finding を構築する。"""
    kpi_ids = _evidence_ids_for_sources(evidence, ("fabric", "web", "local"))
    compliance_ids = _evidence_ids_for_sources(evidence, ("foundry_iq", "azure_ai_search", "local-check"))
    asset_ids = _evidence_ids_for_sources(evidence, ("web", "local", "fabric"))
    all_ids = [str(item.get("id", "")).strip() for item in evidence if str(item.get("id", "")).strip()]

    kpi_score = _unit_score(plan_metrics.get("kpi_evidence_readiness"))
    compliance_score = _unit_score(plan_metrics.get("travel_law_compliance"))
    asset_score = _average([
        _unit_score(asset_metrics.get("value_visibility")),
        _unit_score(asset_metrics.get("trust_signal_presence")),
        _unit_score(asset_metrics.get("disclosure_completeness")),
    ])
    source_score = min(1.0, (len({str(item.get("source", "")) for item in evidence if item.get("source")}) / 3) * 0.7 + (min(len(evidence), 6) / 6) * 0.3) if evidence else -1.0
    chart_score = _average([1.0 if chart.get("data") else 0.6 for chart in charts]) if charts else -1.0

    finding_specs = [
        (
            "evidence-source-coverage",
            "根拠ソースの網羅性",
            source_score,
            "評価に利用できる根拠ソースの件数と種類を確認しました。",
            all_ids[:4],
            "source_coverage",
            "evidence",
        ),
        (
            "kpi-evidence",
            _METRIC_LABELS["kpi_evidence_readiness"],
            kpi_score,
            "KPI の前提・比較軸・根拠説明の明確さを確認しました。",
            kpi_ids,
            "kpi_evidence_readiness",
            "plan",
        ),
        (
            "compliance-evidence",
            _METRIC_LABELS["travel_law_compliance"],
            compliance_score,
            "旅行業法・表示事項に関する根拠とチェック結果を確認しました。",
            compliance_ids,
            "travel_law_compliance",
            "plan",
        ),
        (
            "asset-support",
            "成果物訴求の根拠",
            asset_score,
            "ブローシャの価値訴求・安心材料・表示事項が根拠と整合しているか確認しました。",
            asset_ids,
            "value_visibility",
            "asset",
        ),
        (
            "chart-support",
            _METRIC_LABELS["chart_support"],
            chart_score,
            "数値や比較を補助するチャートが評価に使える状態か確認しました。",
            all_ids[:4],
            "chart_support",
            "evidence",
        ),
    ]

    return [
        {
            "id": finding_id,
            "title": title,
            "status": _finding_status(score),
            "summary": summary,
            "confidence": _finding_confidence(score, evidence_ids),
            "evidence_ids": evidence_ids,
            "metric_key": metric_key,
            "area": area,
        }
        for finding_id, title, score, summary, evidence_ids, metric_key, area in finding_specs
    ]


def _build_evidence_quality_result(
    evidence: list[EvidenceItemPayload],
    charts: list[ChartSpecPayload],
    findings: list[dict],
) -> dict:
    """根拠品質レーンを構築する。"""
    source_count = len({str(item.get("source", "")) for item in evidence if item.get("source")})
    linked_findings = sum(1 for finding in findings if finding.get("evidence_ids"))
    actionable_findings = [finding for finding in findings if finding.get("status") != "na"]
    metrics = {
        "source_coverage": _clone_metric(
            {
                "score": min(1.0, (source_count / 3) * 0.7 + (min(len(evidence), 6) / 6) * 0.3)
                if evidence
                else -1.0,
                "reason": f"{source_count} 種類 / {len(evidence)} 件の根拠を確認しました"
                if evidence
                else "評価に利用できる根拠がありません",
            },
            "source_coverage",
        ),
        "chart_support": _clone_metric(
            {
                "score": _average([1.0 if chart.get("data") else 0.6 for chart in charts]) if charts else -1.0,
                "reason": f"{len(charts)} 件のチャートを確認しました" if charts else "チャートがありません",
            },
            "chart_support",
        ),
        "finding_linkage": _clone_metric(
            {
                "score": round(linked_findings / len(findings), 2) if findings else -1.0,
                "reason": f"{len(findings)} 件中 {linked_findings} 件の指摘が根拠 ID と紐づいています"
                if findings
                else "指摘事項がありません",
            },
            "finding_linkage",
        ),
        "citation_safety": _clone_metric(
            {
                "score": 1.0 if evidence or charts else -1.0,
                "reason": "根拠・チャートは保存前に安全な schema で正規化されています"
                if evidence or charts
                else "安全性を確認する根拠・チャートがありません",
            },
            "citation_safety",
        ),
    }
    category = _build_quality_category(metrics, "根拠表示は安定しています。")
    if actionable_findings:
        weak_findings = [finding["title"] for finding in actionable_findings if finding.get("status") in {"warn", "fail"}]
        if weak_findings:
            category["focus_areas"] = weak_findings[:3]
            category["summary"] = f"根拠の優先確認ポイント: {'、'.join(weak_findings[:3])}"
    return category


def _extract_latest_evaluation_result_for_version(conversation: dict | None, artifact_version: int) -> dict | None:
    """指定 version の最新評価結果を返す。"""
    if not isinstance(conversation, dict):
        return None

    latest_result: dict | None = None
    latest_round = -1
    for event in conversation.get("messages", []):
        if not isinstance(event, dict) or event.get("event") != "evaluation_result":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        version = int(data.get("version", 0) or 0)
        round_number = int(data.get("round", 0) or 0)
        if version != artifact_version or round_number < latest_round:
            continue
        result = data.get("result")
        if not isinstance(result, dict):
            continue
        latest_round = round_number
        latest_result = result
    return latest_result


def _derive_plan_metrics_from_legacy_result(result: dict) -> dict[str, dict]:
    """旧評価結果から plan track を復元する。"""
    metrics: dict[str, dict] = {}

    builtin = result.get("builtin")
    if isinstance(builtin, dict) and "error" not in builtin:
        for key in _PLAN_BUILTIN_METRICS:
            metric = builtin.get(key)
            if isinstance(metric, dict):
                metrics[key] = _clone_metric(metric, key)

    marketing = result.get("marketing_quality")
    if isinstance(marketing, dict):
        for key in _MARKETING_METRICS:
            value = marketing.get(key)
            if isinstance(value, (int, float)):
                metrics[key] = _clone_metric({"score": float(value)}, key)

    custom = result.get("custom")
    if isinstance(custom, dict):
        for key in _PLAN_CUSTOM_METRICS:
            metric = custom.get(key)
            if isinstance(metric, dict):
                metrics[key] = _clone_metric(metric, key)

        for legacy_key, normalized_key in _LEGACY_PLAN_CUSTOM_METRIC_ALIASES.items():
            if normalized_key in metrics:
                continue
            metric = custom.get(legacy_key)
            if isinstance(metric, dict):
                metrics[normalized_key] = _clone_metric(metric, normalized_key)

    return metrics


def _derive_asset_metrics_from_legacy_result(result: dict) -> dict[str, dict]:
    """旧評価結果から asset track を復元する。"""
    metrics: dict[str, dict] = {}
    custom = result.get("custom")
    if not isinstance(custom, dict):
        return metrics

    for key in _ASSET_CUSTOM_METRICS:
        metric = custom.get(key)
        if isinstance(metric, dict):
            metrics[key] = _clone_metric(metric, key)

    if not metrics:
        conversion_metric = custom.get("conversion_potential")
        if isinstance(conversion_metric, dict):
            metrics["value_visibility"] = _clone_metric(conversion_metric, "value_visibility")

    return metrics


def _get_category_metrics_for_comparison(result: dict, category_key: str) -> dict[str, dict]:
    """現行/旧 schema の両方から category metrics を抽出する。"""
    category = result.get(category_key)
    if isinstance(category, dict) and isinstance(category.get("metrics"), dict):
        return {key: dict(metric) for key, metric in category["metrics"].items() if isinstance(metric, dict)}

    if category_key == "plan_quality":
        return _derive_plan_metrics_from_legacy_result(result)
    if category_key == "asset_quality":
        return _derive_asset_metrics_from_legacy_result(result)
    return {}


def _build_regression_guard(current_result: dict, previous_result: dict | None) -> dict:
    """前 version 比の悪化・改善を検出する。"""
    if not isinstance(previous_result, dict):
        return {
            "summary": "比較対象の評価結果がないため、悪化検知は未実行です。",
            "has_regressions": False,
            "degraded_metrics": [],
            "improved_metrics": [],
            "plan_overall_delta": 0.0,
            "asset_overall_delta": 0.0,
        }

    degraded_metrics: list[dict] = []
    improved_metrics: list[dict] = []
    for area, category_key in (("plan", "plan_quality"), ("asset", "asset_quality")):
        current_metrics = _get_category_metrics_for_comparison(current_result, category_key)
        previous_metrics = _get_category_metrics_for_comparison(previous_result, category_key)
        for key, current_metric in current_metrics.items():
            previous_metric = previous_metrics.get(key)
            if not previous_metric:
                continue
            current_score = float(current_metric.get("score", -1) or -1)
            previous_score = float(previous_metric.get("score", -1) or -1)
            if current_score < 0 or previous_score < 0:
                continue
            delta = round(current_score - previous_score, 2)
            if delta <= -0.35:
                degraded_metrics.append(
                    {
                        "key": key,
                        "label": current_metric.get("label", _METRIC_LABELS.get(key, key)),
                        "area": area,
                        "current": current_score,
                        "previous": previous_score,
                        "delta": delta,
                        "severity": "high" if delta <= -1.0 else "medium",
                    }
                )
            elif delta >= 0.35:
                improved_metrics.append(
                    {
                        "key": key,
                        "label": current_metric.get("label", _METRIC_LABELS.get(key, key)),
                        "area": area,
                        "current": current_score,
                        "previous": previous_score,
                        "delta": delta,
                        "severity": "high" if delta >= 1.0 else "medium",
                    }
                )

    degraded_metrics.sort(key=lambda item: item["delta"])
    improved_metrics.sort(key=lambda item: item["delta"], reverse=True)

    current_plan = (
        current_result.get("plan_quality", {}) if isinstance(current_result.get("plan_quality"), dict) else {}
    )
    previous_plan = (
        previous_result.get("plan_quality", {}) if isinstance(previous_result.get("plan_quality"), dict) else {}
    )
    current_asset = (
        current_result.get("asset_quality", {}) if isinstance(current_result.get("asset_quality"), dict) else {}
    )
    previous_asset = (
        previous_result.get("asset_quality", {}) if isinstance(previous_result.get("asset_quality"), dict) else {}
    )
    plan_overall_delta = (
        round(
            float(current_plan.get("overall", -1) or -1) - float(previous_plan.get("overall", -1) or -1),
            2,
        )
        if current_plan and previous_plan
        else 0.0
    )
    asset_overall_delta = (
        round(
            float(current_asset.get("overall", -1) or -1) - float(previous_asset.get("overall", -1) or -1),
            2,
        )
        if current_asset and previous_asset
        else 0.0
    )

    if not degraded_metrics and not improved_metrics:
        summary = "前 version と比較して大きな悪化はありません。"
    else:
        summary = f"悪化 {len(degraded_metrics)} 件 / 改善 {len(improved_metrics)} 件を検出しました。"

    return {
        "summary": summary,
        "has_regressions": bool(degraded_metrics),
        "degraded_metrics": degraded_metrics[:6],
        "improved_metrics": improved_metrics[:6],
        "plan_overall_delta": plan_overall_delta,
        "asset_overall_delta": asset_overall_delta,
    }


async def _persist_evaluation_result(
    conversation_id: str,
    artifact_version: int,
    result: dict,
    owner_id: str | None = None,
) -> dict | None:
    """評価結果を会話イベントとして保存する。"""
    existing = await get_conversation(conversation_id, owner_id=owner_id)
    if not existing:
        return None

    messages = existing.get("messages", [])
    if not isinstance(messages, list):
        messages = []

    completed_versions = sum(1 for event in messages if isinstance(event, dict) and event.get("event") == "done")
    if completed_versions and artifact_version > completed_versions:
        return None

    current_round = 0
    for event in messages:
        if not isinstance(event, dict) or event.get("event") != "evaluation_result":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        try:
            event_version = int(data.get("version", 0) or 0)
        except (TypeError, ValueError):
            continue
        if event_version != artifact_version:
            continue
        current_round = max(current_round, int(data.get("round", 0)))

    evaluation_meta = {
        "version": artifact_version,
        "round": current_round + 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    await append_conversation_events(
        conversation_id=conversation_id,
        user_input=str(existing.get("input", "")),
        new_events=[
            {
                "event": "evaluation_result",
                "data": {
                    **evaluation_meta,
                    "result": result,
                },
            }
        ],
        metrics=existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {},
        status=str(existing.get("status", "completed")),
        owner_id=owner_id or str(existing.get("user_id", "")),
    )
    return evaluation_meta


# --- カスタム評価器（Code-based） ---


def _evaluate_travel_law_compliance(response: str, html: str) -> dict:
    """旅行業法準拠チェック（code-based カスタム評価器）。

    旅行業法で記載が求められる項目の有無をスコア化する。
    """
    required_items = {
        "旅行業登録番号": ["登録番号", "旅行業", "観光庁長官"],
        "取引条件": ["取引条件", "旅行条件", "契約"],
        "取消料": ["取消料", "キャンセル料", "キャンセルポリシー"],
        "旅程": ["日程", "ルート", "行程", "日目"],
        "価格表示": ["円", "税込", "価格", "料金"],
    }
    text = f"{response}\n{html}"
    found = 0
    details: dict[str, bool] = {}
    for item_name, keywords in required_items.items():
        matched = any(kw in text for kw in keywords)
        details[item_name] = matched
        if matched:
            found += 1
    score = found / len(required_items)
    return {
        "score": round(score, 2),
        "details": details,
        "reason": f"{len(required_items)} 項目中 {found} 項目が記載されています",
    }


def _evaluate_brochure_accessibility(html: str) -> dict:
    """旧関数名互換。新しいアクセシビリティ指標を返す。"""
    return _evaluate_accessibility_readiness(html)


def _evaluate_plan_structure(response: str) -> dict:
    """企画書構成チェック（code-based カスタム評価器）。"""
    required_sections = {
        "タイトル": ["#"],
        "キャッチコピー": ["キャッチ", "コピー"],
        "ターゲット": ["ターゲット", "ペルソナ"],
        "プラン概要": ["概要", "日数", "ルート", "日程"],
        "差別化": ["差別化", "独自", "強み"],
        "KPI": ["KPI", "目標", "予約数"],
        "販促チャネル": ["チャネル", "販促", "SNS", "広告"],
        "価格帯": ["円", "価格", "料金"],
    }
    found = 0
    details: dict[str, bool] = {}
    for section, keywords in required_sections.items():
        matched = any(kw in response for kw in keywords)
        details[section] = matched
        if matched:
            found += 1
    score = found / len(required_sections)
    return {
        "score": round(score, 2),
        "details": details,
        "reason": f"{len(required_sections)} 必須セクション中 {found} セクションが含まれています",
    }


def _evaluate_target_fit_readiness(query: str, response: str) -> dict:
    """依頼ターゲットに対する企画の適合性を確認する。"""
    segment = _detect_target_segment(query)
    checks = {
        "ターゲットの明示": _contains_any(response, ("ターゲット", "ペルソナ", "向け")),
        "依頼ターゲットとの整合": _matches_target_segment(segment, response),
        "提供価値の明示": _contains_any(response, ("魅力", "価値", "メリット", "特典", "体験", "安心", "快適", "上質")),
        "条件の具体性": _contains_any(response, ("日程", "価格", "料金", "予約", "定員", "対象", "含まれるもの")),
        "サポート/注意事項": _contains_any(
            response, ("サポート", "案内", "問い合わせ", "注意", "キャンセル", "取消", "安心")
        ),
    }
    return _build_check_metric(checks)


def _evaluate_kpi_evidence_readiness(response: str) -> dict:
    """KPI の数値根拠が十分かを確認する。"""
    checks = {
        "KPI セクション": _contains_any(response, ("KPI", "目標数値")),
        "算定式または前提": _contains_any(response, ("算出", "前提", "想定", "平均単価", "×")),
        "基準値・比較軸": _contains_any(response, ("前年比", "現状", "比較", "レビュー平均", "基準")),
        "対象期間の明示": _contains_any(response, ("期間", "秋季", "出発日", "月次", "週次")),
        "達成条件の具体化": _contains_any(response, ("充足率", "予約完了率", "変更率", "満足度")),
        "根拠説明": _contains_any(response, ("根拠", "理由", "前段分析", "レビュー", "見込める")),
    }
    return _build_check_metric(checks)


def _evaluate_offer_specificity(response: str) -> dict:
    """販売条件とオファー条件の具体性を確認する。"""
    checks = {
        "価格帯": _contains_any(response, ("円", "価格", "料金", "税込")),
        "日程・ルート": _contains_any(response, ("日程", "ルート", "1日目", "2泊3日")),
        "定員・限定条件": _contains_any(response, ("先着", "定員", "各出発日", "限定")),
        "含まれるもの": _contains_any(response, ("含まれるもの", "宿泊", "朝食", "夕食")),
        "含まれないもの": _contains_any(response, ("含まれないもの", "個人的費用", "交通費")),
        "宿泊先・交通": _contains_any(response, ("宿泊先", "ホテル", "交通手段", "貸切バス")),
        "取消条件": _contains_any(response, ("取消料", "キャンセル", "無連絡不参加")),
        "主催者情報": _contains_any(response, ("主催旅行会社", "登録番号", "株式会社")),
    }
    return _build_check_metric(checks)


def _evaluate_cta_visibility(html: str) -> dict:
    """予約導線の見えやすさを評価する。"""
    if not html.strip():
        return _build_check_metric({}, unavailable_reason="ブローシャ HTML が未生成のため未評価です")

    checks = {
        "予約導線": _contains_any(html, ("予約", "申込", "お申し込み", "お問い合わせ")),
        "行動喚起文": _contains_any(html, ("今すぐ", "詳しくはこちら", "空席確認", "ご予約はこちら")),
        "予約方法の明記": _contains_any(
            html,
            ("予約方法", "申込方法", "お申し込み方法", "ご予約は", "予約は", "お問い合わせ窓口", "受付方法"),
        ),
        "連絡先または URL": _contains_any(html, ("http://", "https://", "電話", "メール", "QR")),
    }
    return _build_check_metric(checks)


def _evaluate_value_visibility(html: str) -> dict:
    """価格・限定条件・特典の見えやすさを評価する。"""
    if not html.strip():
        return _build_check_metric({}, unavailable_reason="ブローシャ HTML が未生成のため未評価です")

    checks = {
        "価格表示": _contains_any(html, ("円", "税込", "価格", "料金")),
        "日程表示": _contains_any(html, ("日程", "2泊3日", "3日間", "行程")),
        "含まれるサービス": _contains_any(html, ("含まれるもの", "宿泊", "食事", "朝食", "送迎", "現地サポート")),
        "訴求ポイント": _contains_any(html, ("魅力", "おすすめ", "特典", "安心", "体験", "絶景", "人気")),
    }
    return _build_check_metric(checks)


def _evaluate_trust_signal_presence(html: str) -> dict:
    """安心材料の見えやすさを評価する。"""
    if not html.strip():
        return _build_check_metric({}, unavailable_reason="ブローシャ HTML が未生成のため未評価です")

    checks = {
        "安心訴求": _contains_any(html, ("安心", "サポート", "添乗", "案内付き")),
        "取消・返金": _contains_any(html, ("取消", "キャンセル", "返金")),
        "旅行条件導線": _contains_any(html, ("旅行条件", "取引条件", "契約")),
        "問い合わせ先": _contains_any(html, ("電話", "お問い合わせ", "メール", "窓口")),
    }
    return _build_check_metric(checks)


def _evaluate_accessibility_readiness(html: str) -> dict:
    """ブローシャ HTML のアクセシビリティ準備度を評価する。"""
    if not html.strip():
        return _build_check_metric({}, unavailable_reason="ブローシャ HTML が未生成のため未評価です")

    image_tags = re.findall(r"<img\b[^>]*>", html, re.IGNORECASE)
    images_with_alt = [tag for tag in image_tags if re.search(r"\balt\s*=\s*['\"][^'\"]*['\"]", tag, re.IGNORECASE)]
    checks = {
        "lang 属性": bool(re.search(r"<html[^>]+\blang\s*=", html, re.IGNORECASE)),
        "画像 alt": not image_tags or len(images_with_alt) == len(image_tags),
        "見出し構造": bool(re.search(r"<(h1|h2)\b", html, re.IGNORECASE)),
        "本文セクション構造": bool(re.search(r"<(p|ul|ol|li|section|article)\b", html, re.IGNORECASE)),
        "フッターまたは注意書き": bool(re.search(r"<(footer|small)\b", html, re.IGNORECASE))
        or _contains_any(html, ("旅行条件", "登録番号", "お問い合わせ")),
    }
    return _build_check_metric(checks)


def _evaluate_disclosure_completeness(html: str) -> dict:
    """成果物に必要な表示事項の網羅性を評価する。"""
    if not html.strip():
        return _build_check_metric({}, unavailable_reason="ブローシャ HTML が未生成のため未評価です")

    checks = {
        "旅行業登録番号": _contains_any(html, ("登録番号", "旅行業", "観光庁長官")),
        "主催会社情報": _contains_any(html, ("主催", "会社", "株式会社")),
        "取消料・条件": _contains_any(html, ("取消料", "キャンセル", "取引条件")),
        "価格表示": _contains_any(html, ("円", "税込", "料金")),
        "含まれるもの": _contains_any(html, ("含まれるもの", "宿泊", "食事", "交通")),
    }
    return _build_check_metric(checks)


# --- Built-in 評価器（AI-assisted） ---


async def _run_builtin_evaluators(query: str, response: str) -> dict:
    """azure-ai-evaluation SDK の Built-in 評価器を実行する。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return {"error": "AZURE_AI_PROJECT_ENDPOINT が未設定です"}

    trimmed_query = _truncate_for_evaluation(query, _MAX_EVAL_QUERY_CHARS)
    trimmed_response = _truncate_for_evaluation(response, _MAX_EVAL_RESPONSE_CHARS)

    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"

        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])
        model_config = {
            "azure_endpoint": azure_endpoint,
            "azure_deployment": eval_model,
            "api_version": "2024-10-21",
            "api_key": token.token,
        }

        from azure.ai.evaluation import (
            CoherenceEvaluator,
            FluencyEvaluator,
            RelevanceEvaluator,
            TaskAdherenceEvaluator,
        )

        evaluators = {
            "relevance": RelevanceEvaluator(model_config=model_config, is_reasoning_model=True),
            "coherence": CoherenceEvaluator(model_config=model_config, is_reasoning_model=True),
            "fluency": FluencyEvaluator(model_config=model_config, is_reasoning_model=True),
            "task_adherence": TaskAdherenceEvaluator(model_config=model_config, is_reasoning_model=True),
        }

        results: dict[str, dict] = {}
        for name, evaluator in evaluators.items():
            try:
                result = evaluator(query=trimmed_query, response=trimmed_response)
                score = result.get(name, result.get(f"gpt_{name}"))
                reason = result.get(f"{name}_reason", result.get(f"{name}_label", ""))
                results[name] = {
                    "score": float(score) if score is not None else -1,
                    "reason": str(reason),
                }
            except (ValueError, OSError, RuntimeError) as exc:
                logger.warning("Built-in 評価器 %s でエラー: %s", name, exc)
                results[name] = {"score": -1, "reason": str(exc)}
            except Exception as exc:
                logger.exception("Built-in 評価器 %s で予期しないエラー", name)
                results[name] = {"score": -1, "reason": str(exc)}

        return results

    except ImportError as exc:
        return {"error": f"azure-ai-evaluation が未インストール: {exc}"}
    except (ValueError, OSError) as exc:
        return {"error": f"認証エラー: {exc}"}
    except Exception as exc:
        logger.exception("Built-in 評価器の初期化に失敗")
        return {"error": f"Built-in 評価器の実行に失敗: {exc}"}


# --- Prompt-based カスタム評価器 ---


async def _run_marketing_quality_evaluator(query: str, response: str) -> dict:
    """企画書の総合品質を LLM ジャッジで評価する（prompt-based カスタム評価器）。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return {"score": -1, "reason": "AZURE_AI_PROJECT_ENDPOINT が未設定"}

    trimmed_query = _truncate_for_evaluation(query, _MAX_EVAL_QUERY_CHARS)
    trimmed_response = _truncate_for_evaluation(response, _MAX_EVAL_RESPONSE_CHARS)

    try:
        from src.agent_client import get_shared_credential

        credential = get_shared_credential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"

        from openai import AzureOpenAI

        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])
        client = AzureOpenAI(
            azure_endpoint=azure_endpoint,
            api_key=token.token,
            api_version="2024-10-21",
        )

        judge_prompt = """\
あなたは旅行マーケティング企画書の品質審査官です。
以下のユーザー依頼と企画書を評価し、JSON 形式で結果を返してください。

## 評価基準（各 1〜5 点）
1. **appeal**: 顧客訴求力（キャッチコピーの魅力・ターゲットへの共感度・「行きたい」と思わせる力）
2. **differentiation**: 差別化ポイントの具体性（競合との違いが明確か）
3. **kpi_validity**: KPI の妥当性（測定可能で現実的な目標か）
4. **brand_tone**: ブランド一貫性（トーンの統一・ターゲット層に適した表現・景品表示法 NG 表現の回避）

## ユーザー依頼
{query}

## 企画書
{response}

## 出力形式（JSON のみ、他のテキストは出力しない）
{{
  "appeal": <1-5>,
  "differentiation": <1-5>,
  "kpi_validity": <1-5>,
  "brand_tone": <1-5>,
  "overall": <1-5（4項目の平均）>,
  "reason": "<50文字以内の総合コメント>"
}}"""

        completion = client.chat.completions.create(
            model=eval_model,
            messages=[
                {"role": "system", "content": "JSON のみ出力してください。"},
                {"role": "user", "content": judge_prompt.format(query=trimmed_query, response=trimmed_response)},
            ],
            temperature=0.1,
            max_completion_tokens=500,
        )

        answer = completion.choices[0].message.content or ""
        # JSON 部分を抽出
        json_match = re.search(r"\{[^}]+\}", answer, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"score": -1, "reason": f"JSON パース失敗: {answer[:200]}"}

    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Marketing Quality Evaluator でエラー: %s", exc)
        return {"score": -1, "reason": str(exc)}
    except Exception as exc:
        logger.exception("Marketing Quality Evaluator で予期しないエラー")
        return {"score": -1, "reason": str(exc)}


def _build_plan_quality_result(builtin: dict, marketing_quality: dict, plan_custom_metrics: dict[str, dict]) -> dict:
    """企画書品質レーンを構築する。"""
    metrics: dict[str, dict] = {}

    if isinstance(builtin, dict) and "error" not in builtin:
        for key in _PLAN_BUILTIN_METRICS:
            metric = builtin.get(key)
            if isinstance(metric, dict):
                metrics[key] = _clone_metric(metric, key)

    if isinstance(marketing_quality, dict):
        for key in _MARKETING_METRICS:
            value = marketing_quality.get(key)
            if isinstance(value, (int, float)):
                metrics[key] = _clone_metric(
                    {
                        "score": float(value),
                        "reason": str(marketing_quality.get("reason", "")),
                    },
                    key,
                )

    for key, metric in plan_custom_metrics.items():
        metrics[key] = _clone_metric(metric, key)

    return _build_quality_category(metrics, "主要な企画書観点は安定しています。")


def _build_asset_quality_result(asset_custom_metrics: dict[str, dict]) -> dict:
    """成果物品質レーンを構築する。"""
    metrics = {key: _clone_metric(metric, key) for key, metric in asset_custom_metrics.items()}
    return _build_quality_category(metrics, "主要な成果物観点は安定しています。")


# --- Foundry ポータル連携（クラウド評価） ---


def _evaluation_log_retention_days(value: str | None) -> int:
    """評価ログの保持日数設定を安全な整数に丸める。"""
    try:
        days = int((value or "").strip())
    except ValueError:
        return _DEFAULT_EVALUATION_LOG_RETENTION_DAYS
    if days < 1:
        return _DEFAULT_EVALUATION_LOG_RETENTION_DAYS
    return min(days, 365)


def is_evaluation_logging_enabled(settings: AppSettings | None = None) -> bool:
    """Foundry 評価ログ送信が明示 opt-in かつ送信先設定済みかを返す。"""
    resolved = settings or get_settings()
    return parse_bool_setting(resolved["enable_evaluation_logging"]) and bool(resolved["project_endpoint"].strip())


def _safe_log_text(value: object, limit: int = 80) -> str:
    """ログ用の短い識別子を redaction-aware に正規化する。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if _SENSITIVE_LOG_PATTERN.search(text):
        return "[redacted]"
    normalized = re.sub(r"[\r\n\t]+", " ", text)
    return normalized[:limit]


def _numeric_score(value: object) -> float | None:
    """スコアとして送信してよい数値だけを返す。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return round(float(value), 4)


def _category_metric_scores(category: object) -> dict[str, float]:
    """評価カテゴリから metric 名と数値スコアだけを抽出する。"""
    if not isinstance(category, Mapping):
        return {}
    metrics = category.get("metrics")
    if not isinstance(metrics, Mapping):
        return {}

    sanitized: dict[str, float] = {}
    for key, metric in metrics.items():
        if not isinstance(metric, Mapping):
            continue
        score = _numeric_score(metric.get("score"))
        if score is not None:
            sanitized[_safe_log_text(key, limit=48)] = score
    return sanitized


def _summarize_findings(findings: object) -> list[dict[str, object]]:
    """finding から本文を含まない状態・根拠 ID だけを抽出する。"""
    if not isinstance(findings, list):
        return []

    summarized: list[dict[str, object]] = []
    for finding in findings[:10]:
        if not isinstance(finding, Mapping):
            continue
        evidence_ids = finding.get("evidence_ids")
        summarized.append(
            {
                "id": _safe_log_text(finding.get("id"), limit=64),
                "status": _safe_log_text(finding.get("status"), limit=16),
                "metric_key": _safe_log_text(finding.get("metric_key"), limit=48),
                "area": _safe_log_text(finding.get("area"), limit=24),
                "confidence": _numeric_score(finding.get("confidence")),
                "evidence_ids": [
                    _safe_log_text(evidence_id, limit=64)
                    for evidence_id in evidence_ids[:6]
                    if _safe_log_text(evidence_id, limit=64)
                ]
                if isinstance(evidence_ids, list)
                else [],
            }
        )
    return summarized


def _build_foundry_log_record(
    query: str,
    response: str,
    scores: dict,
    settings: AppSettings | None = None,
) -> dict[str, object]:
    """Foundry へ送る評価ログを raw content なしの最小 payload に変換する。"""
    resolved = settings or get_settings()
    findings = _summarize_findings(scores.get("findings"))
    evidence = scores.get("evidence")
    charts = scores.get("charts")
    regression_guard = scores.get("regression_guard")
    plan_quality = scores.get("plan_quality") if isinstance(scores.get("plan_quality"), Mapping) else {}
    asset_quality = scores.get("asset_quality") if isinstance(scores.get("asset_quality"), Mapping) else {}
    evidence_quality = scores.get("evidence_quality") if isinstance(scores.get("evidence_quality"), Mapping) else {}

    finding_status_counts = {
        status: sum(1 for finding in findings if finding.get("status") == status)
        for status in ("pass", "warn", "fail", "na")
    }
    plan_overall = _numeric_score(plan_quality.get("overall") if isinstance(plan_quality, Mapping) else None)
    asset_overall = _numeric_score(asset_quality.get("overall") if isinstance(asset_quality, Mapping) else None)
    evidence_overall = _numeric_score(evidence_quality.get("overall") if isinstance(evidence_quality, Mapping) else None)
    legacy_overall = _numeric_score(scores.get("legacy_overall"))

    record: dict[str, object] = {
        "schema_version": _EVALUATION_LOG_SCHEMA_VERSION,
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "retention_days": _evaluation_log_retention_days(resolved["evaluation_log_retention_days"]),
        "redaction": {
            "raw_prompt_logged": False,
            "raw_response_logged": False,
            "raw_work_iq_logged": False,
            "transcripts_logged": False,
            "bearer_tokens_logged": False,
            "brochure_html_logged": False,
        },
        "content_shape": {
            "query_chars": len(query),
            "response_chars": len(response),
        },
        "plan_overall": plan_overall if plan_overall is not None else -1.0,
        "asset_overall": asset_overall if asset_overall is not None else -1.0,
        "evidence_overall": evidence_overall if evidence_overall is not None else -1.0,
        "legacy_overall": legacy_overall if legacy_overall is not None else -1.0,
        "metrics": {
            "plan_quality": _category_metric_scores(plan_quality),
            "asset_quality": _category_metric_scores(asset_quality),
            "evidence_quality": _category_metric_scores(evidence_quality),
        },
        "finding_status_counts": finding_status_counts,
        "findings": findings,
        "evidence_count": len(evidence) if isinstance(evidence, list) else 0,
        "chart_count": len(charts) if isinstance(charts, list) else 0,
    }

    if isinstance(regression_guard, Mapping):
        record["regression_guard"] = {
            "has_regressions": bool(regression_guard.get("has_regressions")),
            "degraded_count": len(regression_guard.get("degraded_metrics", []))
            if isinstance(regression_guard.get("degraded_metrics"), list)
            else 0,
            "improved_count": len(regression_guard.get("improved_metrics", []))
            if isinstance(regression_guard.get("improved_metrics"), list)
            else 0,
            "plan_overall_delta": _numeric_score(regression_guard.get("plan_overall_delta")) or 0.0,
            "asset_overall_delta": _numeric_score(regression_guard.get("asset_overall_delta")) or 0.0,
        }

    return record


def _privacy_summary_evaluator(
    plan_overall: float = -1.0,
    asset_overall: float = -1.0,
    evidence_overall: float = -1.0,
    legacy_overall: float = -1.0,
    **_: object,
) -> dict[str, object]:
    """Foundry logging 用のローカル評価器。raw content は受け取らない。"""
    valid_scores = [score for score in (plan_overall, asset_overall, evidence_overall, legacy_overall) if score >= 0]
    if not valid_scores:
        return {"score": -1.0, "reason": "redacted summary only; no aggregate score"}
    return {
        "score": round(sum(valid_scores) / len(valid_scores), 4),
        "reason": "redacted/minimized evaluation summary",
    }


async def _log_to_foundry(record: dict[str, object]) -> str | None:
    """最小化済み評価ログを Foundry ポータルに記録し、ダッシュボード URL を返す。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not is_evaluation_logging_enabled(settings):
        return None

    temp_path: str | None = None
    log_dir: Path | None = None
    try:
        from azure.ai.evaluation import evaluate

        # Foundry project URL を構築
        azure_ai_project = endpoint

        # SDK が file path を要求するため、プロジェクト配下に短命 JSONL を作る。
        log_dir = Path.cwd() / ".evaluation-logs"
        log_dir.mkdir(exist_ok=True)
        temp_file = log_dir / f"evaluation-log-{uuid.uuid4().hex}.jsonl"
        with temp_file.open(mode="w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False)
            f.write("\n")
        temp_path = str(temp_file)

        result = evaluate(
            data=temp_path,
            evaluators={"privacy_summary": _privacy_summary_evaluator},
            azure_ai_project=azure_ai_project,
        )

        # ポータル URL を返す
        studio_url = result.get("studio_url", "")
        if studio_url:
            logger.info("Foundry ポータルに評価結果を記録: %s", studio_url)
            return studio_url
        return None

    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Foundry ポータル連携に失敗: %s", exc)
        return None
    except Exception as exc:
        logger.exception("Foundry ポータル連携で予期しないエラー: %s", exc)
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError as exc:
                logger.warning("評価ログ用の一時ファイル削除に失敗: %s", exc)
        if log_dir:
            try:
                log_dir.rmdir()
            except OSError:
                pass


# --- エンドポイント ---


@router.post("/evaluate")
@limiter.limit("5/minute")
async def evaluate_artifacts(
    request: Request, body: EvaluateRequest, background_tasks: BackgroundTasks
) -> JSONResponse:
    """パイプライン成果物の品質を評価する。

    Built-in 評価器（Relevance / Coherence / Fluency）+
    カスタム評価器（旅行業法準拠 / 企画書構成 / ブローシャアクセシビリティ / 企画書品質）
    でスコアリングし、結果を返す。
    """
    try:
        caller_identity = extract_request_identity(
            request,
            expected_tenant_id=get_settings()["entra_tenant_id"],
            enforce_owner_boundary=bool(body.conversation_id and body.artifact_version),
        )
    except RequestIdentityError as exc:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.message, "code": exc.code})
    results: dict = {}
    conversation: dict | None = None
    evidence, charts = _normalize_evaluation_context(body.evidence, body.charts)

    if body.conversation_id and body.artifact_version:
        try:
            conversation = await get_conversation(body.conversation_id, owner_id=caller_identity["user_id"])
        except (ValueError, OSError) as exc:
            logger.warning("評価用会話履歴の取得に失敗: %s", exc)
        except Exception as exc:
            logger.exception("評価用会話履歴の取得で予期しないエラー: %s", exc)

    if not evidence and not charts and conversation and body.artifact_version:
        evidence, charts = _restore_evaluation_context_for_version(conversation, body.artifact_version)

    # Code-based カスタム評価器（即座に実行）
    plan_custom_metrics = {
        "plan_structure_readiness": _evaluate_plan_structure(body.response),
        "target_fit_readiness": _evaluate_target_fit_readiness(body.query, body.response),
        "kpi_evidence_readiness": _evaluate_kpi_evidence_readiness(body.response),
        "offer_specificity": _evaluate_offer_specificity(body.response),
        "travel_law_compliance": _evaluate_travel_law_compliance(body.response, ""),
    }
    asset_custom_metrics = {
        "cta_visibility": _evaluate_cta_visibility(body.html),
        "value_visibility": _evaluate_value_visibility(body.html),
        "trust_signal_presence": _evaluate_trust_signal_presence(body.html),
        "disclosure_completeness": _evaluate_disclosure_completeness(body.html),
        "accessibility_readiness": _evaluate_accessibility_readiness(body.html),
    }

    results["custom"] = {
        **plan_custom_metrics,
        **asset_custom_metrics,
        "conversion_potential": _build_legacy_conversion_metric(asset_custom_metrics),
    }

    # Built-in AI-assisted 評価器
    try:
        results["builtin"] = await _run_builtin_evaluators(body.query, body.response)
    except (ValueError, OSError, RuntimeError) as exc:
        logger.warning("Built-in 評価器呼び出しに失敗: %s", exc)
        results["builtin"] = {"error": str(exc)}
    except Exception as exc:
        logger.exception("Built-in 評価器呼び出しで予期しないエラー")
        results["builtin"] = {"error": str(exc)}

    # Prompt-based カスタム評価器（LLM ジャッジ）
    try:
        results["marketing_quality"] = await _run_marketing_quality_evaluator(body.query, body.response)
    except (ValueError, OSError, RuntimeError) as exc:
        logger.warning("Marketing Quality Evaluator 呼び出しに失敗: %s", exc)
        results["marketing_quality"] = {"score": -1, "reason": str(exc)}
    except Exception as exc:
        logger.exception("Marketing Quality Evaluator 呼び出しで予期しないエラー")
        results["marketing_quality"] = {"score": -1, "reason": str(exc)}

    results["plan_quality"] = _build_plan_quality_result(
        builtin=results["builtin"],
        marketing_quality=results["marketing_quality"],
        plan_custom_metrics=plan_custom_metrics,
    )
    results["asset_quality"] = _build_asset_quality_result(asset_custom_metrics)
    results["findings"] = _build_evaluation_findings(plan_custom_metrics, asset_custom_metrics, evidence, charts)
    results["evidence_quality"] = _build_evidence_quality_result(evidence, charts, results["findings"])
    if evidence:
        results["evidence"] = evidence
    if charts:
        results["charts"] = charts

    previous_result = None
    if body.conversation_id and body.artifact_version and body.artifact_version > 1:
        try:
            previous_result = _extract_latest_evaluation_result_for_version(
                conversation, body.artifact_version - 1
            )
        except (ValueError, OSError) as exc:
            logger.warning("前 version の評価結果取得に失敗: %s", exc)
        except Exception as exc:
            logger.exception("前 version の評価結果取得で予期しないエラー: %s", exc)

    results["regression_guard"] = _build_regression_guard(results, previous_result)
    results["legacy_overall"] = _average(
        [
            float(results["plan_quality"].get("overall", -1) or -1),
            float(results["asset_quality"].get("overall", -1) or -1),
        ]
    )

    # Foundry ポータル連携は明示 opt-in 時だけ、最小化済み payload で非同期実行する
    if is_evaluation_logging_enabled():
        background_tasks.add_task(_log_to_foundry, _build_foundry_log_record(body.query, body.response, results.copy()))

    monitoring_record = build_evaluation_monitoring_record(
        conversation_id=body.conversation_id,
        artifact_version=body.artifact_version,
        query=body.query,
        response=body.response,
        html=body.html,
        results=results.copy(),
    )
    schedule_continuous_monitoring(
        background_tasks,
        record=monitoring_record,
        sample_key=(
            f"evaluation:{body.conversation_id or 'adhoc'}:"
            f"{body.artifact_version or 0}:{len(body.query)}:{len(body.response)}:{len(body.html)}"
        ),
    )

    evaluation_meta = None
    if body.conversation_id and body.artifact_version:
        try:
            evaluation_meta = await _persist_evaluation_result(
                conversation_id=body.conversation_id,
                artifact_version=body.artifact_version,
                result=results.copy(),
                owner_id=caller_identity["user_id"],
            )
        except (ValueError, OSError) as exc:
            logger.warning("評価結果の保存に失敗: %s", exc)
        except Exception as exc:
            logger.exception("評価結果の保存で予期しないエラー: %s", exc)

    return JSONResponse({**results, "evaluation_meta": evaluation_meta})
