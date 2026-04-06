"""品質評価 API。Built-in + カスタム評価器でパイプライン成果物を評価する。"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.config import get_settings
from src.conversations import append_conversation_events, get_conversation

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
}


class EvaluateRequest(BaseModel):
    """評価リクエスト"""

    query: str = Field(..., description="ユーザーの指示テキスト")
    response: str = Field(..., description="企画書の Markdown テキスト")
    html: str = Field("", description="ブローシャの HTML テキスト（オプション）")
    conversation_id: str | None = Field(default=None, description="保存先の会話ID")
    artifact_version: int | None = Field(default=None, ge=1, description="評価対象の成果物バージョン")


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
) -> dict | None:
    """評価結果を会話イベントとして保存する。"""
    existing = await get_conversation(conversation_id)
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
        if int(data.get("version", 0)) != artifact_version:
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
        "リンクまたはボタン": bool(re.search(r"<(a|button)\b", html, re.IGNORECASE)),
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
        "限定条件": _contains_any(html, ("期間限定", "先着", "限定", "残りわずか", "各出発日")),
        "特典表示": _contains_any(html, ("特典", "無料", "プレゼント", "割引", "早割")),
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
        "リンク/ボタン導線": bool(re.search(r"<(a|button)\b", html, re.IGNORECASE)),
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


async def _log_to_foundry(query: str, response: str, scores: dict) -> str | None:
    """評価結果を Foundry ポータルに記録し、ダッシュボード URL を返す。"""
    settings = get_settings()
    endpoint = settings["project_endpoint"]
    if not endpoint:
        return None

    try:
        from azure.ai.evaluation import evaluate
        from azure.identity import DefaultAzureCredential

        # Foundry project URL を構築
        azure_ai_project = endpoint

        # 一時データファイルを作成
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
            json.dump({"query": query, "response": response}, f, ensure_ascii=False)
            f.write("\n")
            temp_path = f.name

        from azure.ai.evaluation import CoherenceEvaluator, RelevanceEvaluator

        parsed = urlparse(endpoint)
        azure_endpoint = f"{parsed.scheme}://{parsed.netloc}"
        eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", settings["model_name"])

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")

        model_config = {
            "azure_endpoint": azure_endpoint,
            "azure_deployment": eval_model,
            "api_version": "2024-10-21",
            "api_key": token.token,
        }

        result = evaluate(
            data=temp_path,
            evaluators={
                "relevance": RelevanceEvaluator(model_config=model_config, is_reasoning_model=True),
                "coherence": CoherenceEvaluator(model_config=model_config, is_reasoning_model=True),
            },
            azure_ai_project=azure_ai_project,
        )

        # クリーンアップ
        os.unlink(temp_path)

        # ポータル URL を返す
        studio_url = result.get("studio_url", "")
        if studio_url:
            logger.info("Foundry ポータルに評価結果を記録: %s", studio_url)
            return studio_url
        return None

    except (ImportError, ValueError, OSError, RuntimeError) as exc:
        logger.warning("Foundry ポータル連携に失敗: %s", exc)
        return None


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
    results: dict = {}

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

    previous_result = None
    if body.conversation_id and body.artifact_version and body.artifact_version > 1:
        try:
            previous_conversation = await get_conversation(body.conversation_id)
            previous_result = _extract_latest_evaluation_result_for_version(
                previous_conversation, body.artifact_version - 1
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

    # Foundry ポータル連携はレスポンスを待たずに非同期実行する
    background_tasks.add_task(_log_to_foundry, body.query, body.response, results.copy())

    evaluation_meta = None
    if body.conversation_id and body.artifact_version:
        try:
            evaluation_meta = await _persist_evaluation_result(
                conversation_id=body.conversation_id,
                artifact_version=body.artifact_version,
                result=results.copy(),
            )
        except (ValueError, OSError) as exc:
            logger.warning("評価結果の保存に失敗: %s", exc)
        except Exception as exc:
            logger.exception("評価結果の保存で予期しないエラー: %s", exc)

    return JSONResponse({**results, "evaluation_meta": evaluation_meta})
