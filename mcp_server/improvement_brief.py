"""品質評価結果から改善ブリーフを生成する純粋関数。"""

import json
from typing import Any, TypedDict

_LOW_SCORE_THRESHOLD = 4.0
_COMPLIANCE_WARNING_TOKENS = ("⚠", "❌", "違反", "不足", "NG", "注意", "要修正")
_HIDDEN_BUILTIN_METRICS = {"task_adherence", "groundedness"}
_MARKETING_LABELS = {
    "appeal": "訴求力",
    "differentiation": "差別化",
    "kpi_validity": "KPI 妥当性",
    "brand_tone": "ブランドトーン",
}
_METRIC_LABELS = {
    **_MARKETING_LABELS,
    "relevance": "依頼適合性",
    "coherence": "構成の一貫性",
    "fluency": "表現の明瞭さ",
    "plan_structure_readiness": "企画書構成の完成度",
    "senior_fit_readiness": "シニア適合性",
    "kpi_evidence_readiness": "KPI 根拠の明確さ",
    "offer_specificity": "募集条件の具体性",
    "travel_law_compliance": "旅行業法準備度",
    "cta_visibility": "予約導線の明確さ",
    "value_visibility": "オファー訴求の明確さ",
    "trust_signal_presence": "安心材料の見えやすさ",
    "disclosure_completeness": "表示事項の網羅性",
    "accessibility_readiness": "アクセシビリティ準備度",
}
_SECTION_HINTS = ("キャッチコピー", "ターゲット", "差別化", "KPI")


class PriorityIssue(TypedDict):
    """改善の優先課題。"""

    label: str
    reason: str
    suggested_action: str


class ImprovementBriefResult(TypedDict):
    """MCP ツールが返す改善ブリーフ。"""

    evaluation_summary: str
    improvement_brief: str
    priority_issues: list[PriorityIssue]
    must_keep: list[str]


def generate_improvement_brief_result(
    plan_markdown: str,
    evaluation_payload: str = "",
    regulation_summary: str = "",
    rejection_history: str = "",
    user_feedback: str = "",
) -> ImprovementBriefResult:
    """企画書改善用の構造化ブリーフを返す。"""
    evaluation_result = _parse_json_object(evaluation_payload)
    rejection_notes = _parse_json_list(rejection_history)
    priority_issues = _build_priority_issues(
        evaluation_result=evaluation_result,
        regulation_summary=regulation_summary,
        rejection_notes=rejection_notes,
        user_feedback=user_feedback,
    )
    must_keep = _extract_must_keep_elements(plan_markdown)
    evaluation_summary = _build_evaluation_summary(priority_issues, rejection_notes, regulation_summary)
    improvement_brief = _build_improvement_brief(priority_issues, must_keep, regulation_summary)
    return {
        "evaluation_summary": evaluation_summary,
        "improvement_brief": improvement_brief,
        "priority_issues": priority_issues,
        "must_keep": must_keep,
    }


def _parse_json_object(payload: str) -> dict[str, Any]:
    """JSON 文字列を dict に変換する。"""
    if not payload.strip():
        return {}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(payload: str) -> list[str]:
    """JSON 文字列を文字列配列へ変換する。"""
    if not payload.strip():
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    normalized: list[str] = []
    for item in parsed:
        if not isinstance(item, str):
            continue
        stripped = item.strip()
        if stripped:
            normalized.append(stripped)
    return normalized


def _build_priority_issues(
    evaluation_result: dict[str, Any],
    regulation_summary: str,
    rejection_notes: list[str],
    user_feedback: str,
) -> list[PriorityIssue]:
    """評価結果と履歴から改善課題を組み立てる。"""
    issues: list[PriorityIssue] = []

    issues.extend(_build_grouped_priority_issues(evaluation_result))

    builtin = evaluation_result.get("builtin")
    if isinstance(builtin, dict) and "error" not in builtin:
        for metric_name, metric_value in builtin.items():
            if metric_name in _HIDDEN_BUILTIN_METRICS or not isinstance(metric_value, dict):
                continue
            score = metric_value.get("score")
            if not isinstance(score, (int, float)) or score < 0 or score >= _LOW_SCORE_THRESHOLD:
                continue
            reason = str(metric_value.get("reason") or "根拠が不足しています")
            issues.append(
                {
                    "label": _humanize_metric(metric_name),
                    "reason": f"スコア {score:.1f}/5。{reason}",
                    "suggested_action": f"{_humanize_metric(metric_name)}を上げる具体表現・根拠・ベネフィットを補強する",
                }
            )

    marketing_quality = evaluation_result.get("marketing_quality")
    if isinstance(marketing_quality, dict):
        for metric_name, label in _MARKETING_LABELS.items():
            score = marketing_quality.get(metric_name)
            if not isinstance(score, (int, float)) or score >= _LOW_SCORE_THRESHOLD:
                continue
            issues.append(
                {
                    "label": label,
                    "reason": f"スコア {score:.1f}/5。マーケティング観点で改善余地があります",
                    "suggested_action": f"{label}が上がるように訴求軸と比較優位を明確にする",
                }
            )

        review_reason = marketing_quality.get("reason")
        if isinstance(review_reason, str) and review_reason.strip():
            issues.append(
                {
                    "label": "審査コメント",
                    "reason": review_reason.strip(),
                    "suggested_action": "レビューコメントをそのまま反映し、曖昧な表現を減らす",
                }
            )

    custom = evaluation_result.get("custom")
    if isinstance(custom, dict):
        for metric_name, metric_value in custom.items():
            if not isinstance(metric_value, dict):
                continue
            details = metric_value.get("details")
            if not isinstance(details, dict):
                continue
            missing_items = [item for item, passed in details.items() if passed is False]
            if not missing_items:
                continue
            issues.append(
                {
                    "label": _humanize_metric(metric_name),
                    "reason": f"未充足項目: {'・'.join(sorted(missing_items))}",
                    "suggested_action": "不足している必須要素を追記し、読み手が確認できる形に整理する",
                }
            )

    normalized_regulation = regulation_summary.strip()
    if normalized_regulation and any(token in normalized_regulation for token in _COMPLIANCE_WARNING_TOKENS):
        issues.append(
            {
                "label": "規制・表現リスク",
                "reason": "規制チェック結果に注意または違反候補が含まれています",
                "suggested_action": "誇大表現を避け、必要な注意書きや条件の明示を残したまま文面を整える",
            }
        )

    if rejection_notes:
        issues.append(
            {
                "label": "差し戻し履歴",
                "reason": " / ".join(rejection_notes[-2:]),
                "suggested_action": "過去の差し戻し理由を再発させないように、構成と訴求を調整する",
            }
        )

    stripped_feedback = user_feedback.strip()
    if stripped_feedback and not stripped_feedback.startswith("以下の品質評価結果に基づいて企画書を改善してください"):
        issues.append(
            {
                "label": "追加フィードバック",
                "reason": stripped_feedback,
                "suggested_action": "ユーザーの追加指示を優先度高く反映する",
            }
        )

    deduped = _dedupe_issues(issues)
    if deduped:
        return deduped[:5]

    return [
        {
            "label": "完成度の底上げ",
            "reason": "重大な欠点は見当たらないため、説得力と具体性をさらに高める段階です",
            "suggested_action": "ターゲットの解像度、差別化の根拠、CTA の具体性を強める",
        }
    ]


def _dedupe_issues(issues: list[PriorityIssue]) -> list[PriorityIssue]:
    """重複課題をラベルと理由でまとめる。"""
    deduped: list[PriorityIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue["label"], issue["reason"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _build_grouped_priority_issues(evaluation_result: dict[str, Any]) -> list[PriorityIssue]:
    """新しい grouped evaluation schema から優先課題を抽出する。"""
    issues: list[PriorityIssue] = []

    regression_guard = evaluation_result.get("regression_guard")
    if isinstance(regression_guard, dict):
        degraded_metrics = regression_guard.get("degraded_metrics")
        if isinstance(degraded_metrics, list):
            for metric in degraded_metrics[:2]:
                if not isinstance(metric, dict):
                    continue
                label = str(metric.get("label") or _humanize_metric(str(metric.get("key") or "悪化項目")))
                current = metric.get("current")
                previous = metric.get("previous")
                if isinstance(current, (int, float)) and isinstance(previous, (int, float)):
                    reason = f"前ラウンド比で {previous:.1f}→{current:.1f} に低下"
                else:
                    reason = "前ラウンドより品質が低下しています"
                issues.append(
                    {
                        "label": f"悪化検知: {label}",
                        "reason": reason,
                        "suggested_action": "前ラウンドで満たせていた水準を下回らないように、まずこの項目を回復する",
                    }
                )

    issues.extend(_collect_group_track_issues(evaluation_result.get("plan_quality"), area="plan", limit=3))
    issues.extend(_collect_group_track_issues(evaluation_result.get("asset_quality"), area="asset", limit=2))
    return issues


def _collect_group_track_issues(track: Any, area: str, limit: int) -> list[PriorityIssue]:
    """grouped quality track から低スコア課題を抽出する。"""
    if not isinstance(track, dict):
        return []

    metrics = track.get("metrics")
    if not isinstance(metrics, dict):
        return []

    ranked: list[tuple[str, dict[str, Any]]] = []
    for key, metric in metrics.items():
        if not isinstance(metric, dict):
            continue
        score = metric.get("score")
        if not isinstance(score, (int, float)) or score < 0 or score >= _LOW_SCORE_THRESHOLD:
            continue
        ranked.append((str(key), metric))

    ranked.sort(key=lambda item: float(item[1].get("score", 999)))

    issues: list[PriorityIssue] = []
    for key, metric in ranked[:limit]:
        label = str(metric.get("label") or _humanize_metric(key))
        reason = str(metric.get("reason") or f"スコア {float(metric['score']):.1f}/5")
        if area == "plan":
            action = "企画書本文で根拠・条件・訴求を具体化し、改善後も構成の一貫性を維持する"
        else:
            action = "企画書の訴求と成果物の見せ方を揃え、ブローシャ上でも伝わる文言と表示要素へ落とし込む"
        issues.append(
            {
                "label": label,
                "reason": reason,
                "suggested_action": action,
            }
        )
    return issues


def _build_evaluation_summary(
    priority_issues: list[PriorityIssue],
    rejection_notes: list[str],
    regulation_summary: str,
) -> str:
    """評価サマリ文を生成する。"""
    compliance_count = (
        1
        if regulation_summary.strip() and any(token in regulation_summary for token in _COMPLIANCE_WARNING_TOKENS)
        else 0
    )
    return (
        f"優先課題 {len(priority_issues)} 件、差し戻し履歴 {len(rejection_notes)} 件、"
        f"コンプライアンス注意 {compliance_count} 件を検出しました。"
    )


def _build_improvement_brief(
    priority_issues: list[PriorityIssue],
    must_keep: list[str],
    regulation_summary: str,
) -> str:
    """LLM に渡す要約ブリーフ文を生成する。"""
    lines = ["次の優先順で企画書を改善してください。"]
    for index, issue in enumerate(priority_issues, start=1):
        lines.append(f"{index}. {issue['label']} - {issue['suggested_action']}（{issue['reason']}）")
    if must_keep:
        lines.append(f"維持すべき要素: {' / '.join(must_keep)}")
    if regulation_summary.strip() and any(token in regulation_summary for token in _COMPLIANCE_WARNING_TOKENS):
        lines.append("表現ルール: 最上級表現や断定表現は避け、必要な注意書き・条件は削除しない")
    lines.append("出力方針: 元の強みは残しつつ、課題箇所だけを具体化して再構成する")
    return "\n".join(lines)


def _extract_must_keep_elements(plan_markdown: str) -> list[str]:
    """企画書内で残すべき核を抽出する。"""
    lines = [line.strip() for line in plan_markdown.splitlines() if line.strip()]
    keep: list[str] = []

    title = next((line.lstrip("# ") for line in lines if line.startswith("#")), "")
    if title:
        keep.append(f"タイトル: {title}")

    for hint in _SECTION_HINTS:
        excerpt = _extract_section_excerpt(plan_markdown, hint)
        if excerpt:
            keep.append(f"{hint}: {excerpt}")

    if not keep and lines:
        keep.append(f"主題: {lines[0][:80]}")

    return keep[:4]


def _extract_section_excerpt(plan_markdown: str, heading_hint: str) -> str:
    """見出しに紐づく短い抜粋を返す。"""
    lines = plan_markdown.splitlines()
    start_index = -1
    for index, line in enumerate(lines):
        if line.strip().startswith("#") and heading_hint in line:
            start_index = index + 1
            break
    if start_index < 0:
        return ""

    for line in lines[start_index:]:
        stripped = line.strip().lstrip("- ").strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        return stripped[:100]
    return ""


def _humanize_metric(metric_name: str) -> str:
    """評価指標名を読みやすい表現へ寄せる。"""
    label = _METRIC_LABELS.get(metric_name)
    if label:
        return label
    return metric_name.replace("_", " ")
