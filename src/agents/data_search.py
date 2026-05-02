"""Agent1: データ検索エージェント。Fabric Lakehouse から販売・顧客データを検索・分析する。"""

import contextvars
import csv
import json
import logging
import os
import re
import struct
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_framework import tool
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import CredentialUnavailableError, DefaultAzureCredential
from pydantic import BaseModel

from src.config import get_settings
from src.tool_telemetry import build_tool_event_data, emit_tool_event, redact_sensitive_text, trace_tool_invocation

try:
    import pyodbc

    _HAS_PYODBC = True
except ImportError:
    _HAS_PYODBC = False

logger = logging.getLogger(__name__)
_SQL_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")

# Original user prompt context (rubber-duck `agent1-da-prompt-preserve` 2026-05-02 BLOCKING #2):
# Agent1 (data-search-agent) の LLM が tool 引数として `query_data_agent(question)` に渡す文字列を
# 抽象化・前置き付加してしまい、ユーザの explicit filters (夏 / ハワイ / 学生 等) が drop される
# 問題に対する code-level safeguard。`workflow_event_generator` の `agent.run(user_input)` 直前で
# 元のユーザプロンプトを ContextVar に set し、`query_data_agent` の structured retry 時に
# 元プロンプトから filters を抽出する。LLM 任せの抽象化ではなく、必ず元プロンプトの言葉から
# canonical filters を導出することで「家族構成 → family」のような誤抽出を防ぐ。
_original_user_prompt: contextvars.ContextVar[str] = contextvars.ContextVar(
    "data_search.original_user_prompt", default=""
)


@contextmanager
def original_user_prompt_context(prompt: str):
    """`agent.run(user_input)` の前後で元ユーザプロンプトを ContextVar に保持する。

    `query_data_agent` の structured retry が、LLM が rewrite した tool 引数ではなく
    元のユーザプロンプトから filters を抽出するために使う。
    """
    token = _original_user_prompt.set(prompt or "")
    try:
        yield
    finally:
        _original_user_prompt.reset(token)


def _get_original_user_prompt() -> str:
    """Return the original user prompt set via `original_user_prompt_context`, or empty."""
    return _original_user_prompt.get() or ""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_evidence_quote(value: object, *, max_length: int = 220) -> str:
    normalized = redact_sensitive_text(str(value or "").replace("\n", " ").strip())
    return f"{normalized[: max_length - 1]}…" if len(normalized) > max_length else normalized


_LOW_CONFIDENCE_DATA_AGENT_PATTERNS = (
    "分析を実行できませんでした",
    "抽出できませんでした",
    "データ未表示",
    "データなし",
    "安全に算出できるデータなし",
    "提示できません",
    "表示できません",
    "見つかりませんでした",
    "見つかりません",
    "存在しませんでした",
    "利用可能なデータが無い",
    "確認できませんでした",
    "取得できません",
    "取得できませんでした",
    # Japanese particle 「が」 variants. Data Agent often emits
    # 「実データの取得**が**できませんでした」(with が) which is NOT a
    # substring of 「取得できません」. Add explicit variants so the
    # low-confidence detector triggers SQL fallback for these polite
    # 「we couldn't find data」 messages observed live (2026-05-01).
    "取得ができません",
    "取得ができませんでした",
    "実データの取得ができません",
    "実データの取得ができませんでした",
    "技術的な理由",
    "技術的なエラー",
    "技術的な都合",
    "技術的都合",
    "システム的なエラー",
    "システム的エラー",
    "システムエラー",
    "システム的な制約",
    "データ抽出ができません",
    "データ抽出ができませんでした",
    "エラーが発生",
    "データ取得プロセスでエラー",
    "詳細データ取得ができません",
    "詳細データ取得ができませんでした",
    "具体的な分析結果は取得できません",
    "具体的な分析結果は取得できませんでした",
    "内部エラー",
    "問題が発生",
    "クエリを実行しましたが",
    "条件に一致",
    "追加提示してください",
    "必要であれば",
    "ご希望があれば",
    "ご希望の場合",
    "技術的な制約",
    "技術的制約",
    "技術的な理由",
    "集計できません",
    "集計不可",
    "抽出できません",
    "全エリア・全年齢層",
    "旅行先・カテゴリ・年齢層の指定なし",
    "gql",
    "graphql",
    "json",
    "specific data is unavailable",
    "could not find",
    "not found",
    "no matching",
    "unable to",
)
_PLACEHOLDER_DATA_AGENT_PATTERNS = (
    "¥x",
    "￥x",
    "x,xxx",
    "xx件",
    "xxx人",
    "xx％",
    "xx%",
    "○○",
    "旅行先a",
    "例のフォーマット",
    "数値・内容例",
    "具体例です",
    "分析例",
)
# 強い失敗表明: 説明的な数値（「20代」「2人以上のグループ」など）が混在していても、
# 取得不能を明示する文面はそれ自体で低信頼として扱う。
_STRONG_DATA_AGENT_FAILURE_PATTERNS = (
    "技術的なエラー",
    "技術的な都合",
    "技術的都合",
    "システム的なエラー",
    "システム的エラー",
    "システムエラー",
    "システム的な制約",
    "エラーが発生し",
    "エラーが発生したため",
    "データ取得プロセスでエラー",
    "データ抽出ができません",
    "データ抽出ができませんでした",
    "詳細データ取得ができません",
    "詳細データ取得ができませんでした",
    "具体的な分析結果は取得できません",
    "具体的な分析結果は取得できませんでした",
    # Japanese particle 「が」 variants observed live (2026-05-01) for
    # 「現在、ハワイ学生旅行（夏季）に関する実データの取得**が**できませんでした」.
    # 「取得できません」(no が) does NOT match this. Add explicit が forms so
    # the SQL fallback triggers and the polite "no data" never reaches the UI.
    "取得ができません",
    "取得ができませんでした",
    "実データの取得ができません",
    "実データの取得ができませんでした",
    "詳細な数値やランキング",
    # 2026-05-01 condition matrix で観測された新しいソフト謝罪文言。
    # 例 (春のパリ): "システムで集計を試みましたが、…内部の仕組み上エラーが発生しました。"
    "内部の仕組み上エラー",
    "内部の仕組みでエラー",
    "内部の仕組み上の制約",
    "システムの内部仕組み",
    "システムの仕様上の制約",
)
# Fabric Data Agent インフラ層 (NL2Ontology / NL2SQL / Fabric backend) が返す
# 英語の内部エラーフレーズ。これらは正規化済み (lower-case) 文字列に対して照合する。
# Live demo (2026-04-30 05:33 UTC, conv f94774cc) で観測された例:
#   "Failed to generate query. The error was: Failed to generate NL2Ontology query
#    with error \"{\"code\":\"InternalError\",\"subCode\":0,\"message\":\"An internal error...\"}\""
# これらが evidence card の quote として 0.85 信頼で出てしまっていたため、
# 早い段階で低信頼判定して Fabric SQL 補強カードに置き換える。
_STRONG_DATA_AGENT_FAILURE_PATTERNS_EN = (
    "failed to generate query",
    "failed to generate nl2ontology",
    "failed to generate nl2sql",
    "nl2ontology query",
    "nl2sql query",
    "internalerror",
    "an internal error occurred",
    "an internal error has occurred",
    "an internal error...",
    '"code":"internalerror"',
    "subcode",
)
_MISSING_SALES_DATA_AGENT_PATTERNS = (
    "売上実績",
    "売上上位",
    "合計売上",
    "予約数",
    "予約件数",
    "合計人数",
)
# 真のインフラ層エラー (クエリパス自体が壊れた): grounded metric が混在していても低信頼扱い。
# `_STRONG_DATA_AGENT_FAILURE_PATTERNS` の subset として、明示的に「エラー」「障害」と
# 宣言している HARD failures のみ列挙する。partial-data caveat (実データの取得ができません等) は
# SOFT として、grounded override より後でチェックする。
_HARD_INFRA_FAILURE_PATTERNS_JP = (
    "技術的なエラー",
    "システム的なエラー",
    "システム的エラー",
    "システムエラー",
    "サーバーエラー",
    "サービス障害",
    "障害が発生",
    "エラーが発生し",
    "エラーが発生したため",
    "データ取得プロセスでエラー",
    "内部の仕組み上エラー",
    "内部の仕組みでエラー",
)
# フィルタ無視: ユーザ指定条件を勝手に外して回答した場合は、grounded metric があっても
# 低信頼扱い (rubber-duck `grounded-metrics-fix-review` 2026-05-02)。
_HARD_IGNORED_FILTER_PATTERNS = (
    "全エリア・全年齢層",
    "旅行先・カテゴリ・年齢層の指定なし",
)
# Yen 金額判定: ¥/￥ 接頭または 円 接尾の 4 桁以上の金額 (¥0 / ¥123 等の trivial を除外)。
# 「¥38,926,615」「1,022,000 円」「¥38000000」等を grounded amount として認識する。
_YEN_AMOUNT_RE = re.compile(
    r"(?:[¥￥]\s*(?:\d{1,3}(?:,\d{3})+|\d{4,})|(?:\d{1,3}(?:,\d{3})+|\d{4,})\s*円)"
)
# 件数 metric: 件/名/人 を伴う数値表現。`泊` (期間表現) や `本` は集計指標とは限らないので
# grounded override を判定する目的では使わない (rubber-duck `grounded-metrics-impl-review`
# 2026-05-02 BLOCKING fix)。
_COUNT_METRIC_RE = re.compile(r"\d[\d,]*\s*(?:件|名|人)")

_DATA_AGENT_RESULT_TOOL_NAMES = {
    "trace.analyze_ontology",
    "analyze.database.execute",
}
# Fabric Data Agent v2 (Travel_Ontology_DA_v2) は ontology + nl2code + execute の
# 多段ツール呼び出しで 180-220 秒かかることがある。150 秒だと "年別の売上トレンド"
# のようなクロス集計プロンプトが in_progress のまま打ち切られ、SQL fallback に逃げて
# しまう (2026-04-30 live probe で U1=198s, P11=144s)。240 秒まで広げて、デモで
# よくある時系列・複合集計プロンプトを Data Agent 経由で完走させる。
_DATA_AGENT_POLL_TIMEOUT_SECONDS = 240
_KNOWN_DESTINATIONS = (
    "沖縄",
    "北海道",
    "京都",
    "大阪",
    "東京",
    "ハワイ",
    "パリ",
    "ニューヨーク",
    "オーストラリア",
    "ローマ",
    "台湾",
    "韓国",
)


def _has_yen_amount(answer: str) -> bool:
    """¥/￥ 接頭または 円 接尾の 4 桁以上の金額が含まれるか。"""
    return bool(_YEN_AMOUNT_RE.search(answer))


def _has_count_metric(answer: str) -> bool:
    """件/名/人/本/泊 を伴う数値 metric が含まれるか。"""
    return bool(_COUNT_METRIC_RE.search(answer))


def _has_grounded_metrics(answer: str) -> bool:
    """Data Agent が ¥ 金額と件/名/人 系 metric を **共に** 含む実データ回答を返したか。

    Soft な「ご希望があれば」「集計できません」「実データの取得ができません」型 disclaimer が
    grounded narrative 内に混在していても、両 metric が揃っていれば partial answer の honest
    caveat として扱い、failure ではないとみなす (rubber-duck `grounded-metrics-fix-review`
    2026-05-02 採用)。¥0 / ¥123 のような桁不足金額は `_YEN_AMOUNT_RE` で除外済。
    `泊` (例: 2泊3日) は期間表現なので grounded override の count metric としては使わない
    (rubber-duck `grounded-metrics-impl-review` 2026-05-02 BLOCKING fix)。
    """
    return _has_yen_amount(answer) and _has_count_metric(answer)


def _is_low_confidence_data_agent_answer(answer: str) -> bool:
    """Data Agent が接続成功でも実質的に分析できていない回答を検出する。"""
    normalized = answer.lower()
    if not normalized.strip():
        return True
    # GraphQL / SQL 生クエリ leak は失敗扱い。
    if re.search(r"```(?:json|gql|graphql)\b|^\s*[{[]\s*\"|query\s*[{(]", answer, re.IGNORECASE | re.MULTILINE):
        return True
    if any(pattern in normalized for pattern in _PLACEHOLDER_DATA_AGENT_PATTERNS):
        return True
    # 1) 英語のインフラ層エラー (NL2Ontology / NL2SQL / InternalError):
    #    クエリパス自体が失敗しているので "subCode:0" などの数字を含んでも低信頼。
    if any(pattern in normalized for pattern in _STRONG_DATA_AGENT_FAILURE_PATTERNS_EN):
        return True
    # 2) 日本語のインフラ層エラー (技術的なエラー / システムエラー / 障害):
    #    grounded metric があっても明示的な error 宣言は最優先で低信頼扱い。
    if any(pattern in answer for pattern in _HARD_INFRA_FAILURE_PATTERNS_JP):
        return True
    # 3) フィルタ無視: ユーザ指定条件を勝手に外した広域回答は、売上数値があっても
    #    「ユーザの聞きたかった分析」ではないので低信頼扱い。
    if any(pattern in answer for pattern in _HARD_IGNORED_FILTER_PATTERNS):
        return True
    # 4) Grounded metric override: ¥ 金額 + 件/名/人 metric の両方を含む DA narrative には
    #    "ご希望があれば" "抽出できません" "実データの取得ができません" 等の soft disclaimer が
    #    頻繁に混在する。これらは partial answer の honest caveat であって failure ではないので、
    #    両 metric が揃っているとき soft 判定 (5/6/7) を bypass して高信頼扱いする。
    if _has_grounded_metrics(answer):
        return False
    # 5) Soft な失敗表明 (実データの取得ができません / データ抽出ができません / 詳細データ取得不可 等)
    if any(pattern in answer for pattern in _STRONG_DATA_AGENT_FAILURE_PATTERNS):
        return True
    has_specific_metric = bool(re.search(r"\d[\d,]*(?:\s*)(?:円|件|名|人|★|/5)", answer))
    # 6) 売上系の missing-data 説明: grounded metric があれば救う、無ければ低信頼。
    if any(pattern in answer for pattern in _MISSING_SALES_DATA_AGENT_PATTERNS) and (
        "データなし" in answer
        or "データ不足" in answer
        or "見つからなかった" in answer
        or "見つかりません" in answer
        or "存在しません" in answer
        or "利用可能なデータが無い" in answer
        or "技術的な制約" in answer
        or "技術的制約" in answer
        or "技術的な理由" in answer
        or "集計できません" in answer
        or "集計不可" in answer
    ):
        return not _has_grounded_metrics(answer)
    # 7) 弱表現フォールバック: 数字を全く含まない polite "no data" 回答を低信頼にする。
    has_weak_phrase = any(pattern.lower() in normalized for pattern in _LOW_CONFIDENCE_DATA_AGENT_PATTERNS)
    return has_weak_phrase and not has_specific_metric


def _select_data_agent_answer(assistant_messages: list[str]) -> str:
    """assistant が複数メッセージを emit した場合に最終回答を選ぶ。

    Data Agent は self-retry の過程で「技術的なエラーが発生したので分解します」のような
    中間ステータスメッセージを途中で出すことがある。全メッセージを単純結合すると、
    最終メッセージが成功（具体数値あり）でも強い失敗フレーズで低信頼判定される。
    最終メッセージが高信頼ならそれを単独で返し、そうでなければ全結合を返す。
    """
    if not assistant_messages:
        return ""
    final_answer = assistant_messages[-1].strip()
    if final_answer and not _is_low_confidence_data_agent_answer(final_answer):
        return final_answer
    return "\n".join(assistant_messages).strip()


def _resolve_fabric_data_agent_runtime() -> str:
    """Fabric Data Agent REST を使うか、安定した SQL 経路を優先するかを返す。"""
    raw = str(get_settings().get("fabric_data_agent_runtime", "") or "").strip().lower()
    if raw in {"true", "1", "yes", "enabled", "rest", "data_agent"}:
        return "rest"
    if raw in {"auto"}:
        return "rest"
    return "sql"


def _resolve_data_agent_version() -> str:
    """Fabric Data Agent v1 / v2 を選ぶ。default は v1（既存挙動を保護）。

    Phase 9 で導入した v2 (Travel_Ontology_DA_v2 + lh_travel_marketing_v2) は
    `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` を環境変数に設定して有効化する。
    `FABRIC_DATA_AGENT_URL_V2` が未設定の場合は v1 にフォールバックする。
    """
    raw = str(get_settings().get("fabric_data_agent_runtime_version", "") or "").strip().lower()
    if raw in {"v2", "2", "lh_v2", "travel_ontology_da_v2"}:
        v2_url = str(get_settings().get("fabric_data_agent_url_v2", "") or "").strip()
        if v2_url:
            return "v2"
        logger.warning(
            "FABRIC_DATA_AGENT_RUNTIME_VERSION=v2 が指定されたが FABRIC_DATA_AGENT_URL_V2 が未設定。v1 にフォールバックします。"
        )
    return "v1"


def _resolve_data_agent_url(version: str) -> str:
    """version に対応する Fabric Data Agent published URL を返す。"""
    settings = get_settings()
    if version == "v2":
        return str(settings.get("fabric_data_agent_url_v2", "") or "").strip()
    return str(settings.get("fabric_data_agent_url", "") or "").strip()


def _build_data_agent_question_v2(question: str) -> str:
    """v2 用の質問プロンプト。

    v2 (Travel_Ontology_DA_v2) は Phase 9.6 で aiInstructions v6 (~16KB) に値マッピング・
    時系列 SQL テンプレート・計算指標 SQL ロジック・失敗時リカバリ手順・回答フォーマット
    ガイド・プレースホルダー禁止ガードを **すべて内蔵済み** (`scripts/fabric_data_overhaul/
    v2_artifacts/aiInstructions_v6.md` 参照)。

    2026-05-02 の live App Insights ログ (587-char 質問 → 219-char polite refusal) と、
    standalone probe (`scripts/fabric_data_overhaul/v2_artifacts/probe_user_prompt.py`) で
    raw 38-char 質問 → 268-char rich grounded answer (¥38.9M / 39件 / 131名) を取得した
    比較から、アプリ側で長い preamble を重ねると NL2Ontology が confuse して低信頼応答を
    返すことが判明した。よって v2 では **質問のみ** を渡す。

    v1 (`Travel_Ontology_DA`、travel_sales / travel_review schema) は別関数
    `_build_data_agent_question` を引き続き使う。v1 の aiInstructions は v2 ほど richly
    populated されておらず、アプリ側 preamble に依存しているため。
    """
    return question


# Fabric Lakehouse v2 で実際に格納されている canonical 値。NL2Ontology が
# 日本語フリーテキストから 3 条件以上 conjoined filter を組み立てると失敗
# (Phase 10 P02 「夏のハワイの売上」= no_data・GQL 0件) するので、app 側で
# 1 ヒットだけ確実な日本語語彙を canonical 英語値に正規化して、structured
# retry prompt で明示的に渡す。
#
# 設計指針 (rubber-duck 監査 2026-05-02):
# - **明示語のみ**: 「若い旅行者」「ビジネス利用」のような曖昧語は誤マッピング
#   リスクが高いので入れない
# - **destination_region は触らない**: Phase 10 P01「ハワイの売上」=A なので
#   日本語のまま動いている。手を出すと回帰
# - **0 ヒット / 2+ ヒット は None 扱い**: 1 ヒットだけ正規化することで
#   「学生グループ」「ファミリーとカップル比較」のような複合クエリで誤注釈
#   しないようにする
_DATA_AGENT_SEGMENT_NORMALIZATION = {
    "学生": "student",
    "ファミリー": "family",
    "家族": "family",
    "子連れ": "family",
    "カップル": "couple",
    "二人旅": "couple",
    "一人旅": "solo",
    "ソロ": "solo",
    "団体": "group",
    "グループ": "group",
    "シニア": "senior",
    "出張": "business",
    "ビジネス出張": "business",
}
_DATA_AGENT_SEASON_NORMALIZATION = {
    "春": "spring",
    "夏": "summer",
    "秋": "autumn",
    "冬": "winter",
}


def _extract_normalized_filters(question: str) -> dict[str, str] | None:
    """日本語の segment / season を Fabric lakehouse v2 の英語 canonical 値に正規化する。

    Returns:
        - 1 dimension あたり 1 ヒットだけ確認できた filter dict (例: {"customer_segment": "student", "season": "summer"})
        - 曖昧 (0 ヒット or 2+ ヒット) または何もマッチしないときは None
    """
    seg_hits: set[str] = set()
    for ja, en in _DATA_AGENT_SEGMENT_NORMALIZATION.items():
        if ja in question:
            seg_hits.add(en)
    if len(seg_hits) >= 2:
        # 「ファミリーとカップル比較」のような明示的比較クエリは正規化しない
        return None

    season_hits: set[str] = set()
    for ja, en in _DATA_AGENT_SEASON_NORMALIZATION.items():
        if ja in question:
            season_hits.add(en)
    if len(season_hits) >= 2:
        return None

    filters: dict[str, str] = {}
    if len(seg_hits) == 1:
        filters["customer_segment"] = next(iter(seg_hits))
    if len(season_hits) == 1:
        filters["season"] = next(iter(season_hits))
    return filters or None


def _build_structured_retry_question(question: str, filters: dict[str, str]) -> str:
    """1 回目低信頼後の structured retry プロンプト。

    Fabric Data Agent の NL2Ontology が日本語フリーテキストから conjoined filter を
    取りこぼす問題 (Phase 10 P02/P07) を回避するため、正規化済み英語値を箇条書きで
    schema-aware に明示する。

    rubber-duck 監査 2026-05-02 を反映:
    - **緩和は禁止**: 「条件を勝手に緩和せず exact で 0 件のときは『該当 0 件』と明示」
      (緩和すると Data Agent が「正確に動いた」ことを検証できなくなる)
    - **正規化値は英語小文字のまま使う**ことを明示 (NL2Ontology の正規化バイパス)
    """
    lines = [
        "前回の質問では実データを取得できませんでした。",
        "下記の正規化済みフィルタを必ず厳密に適用して、もう一度実データを検索してください。",
        "",
        "正規化済みフィルタ条件:",
    ]
    for key, value in filters.items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "上記の値はそのまま英語小文字で WHERE 句に使ってください (例: `customer_segment = 'student'`)。",
            "条件を勝手に緩和せず、exact 条件で 0 件のときは「該当 0 件」と明示してください (近接データへの自動置換は禁止)。",
            "実データが取れた場合は通常通り 1) 結論、2) 適用条件、3) 主要指標、4) 表/ランキング、5) 補足の順でまとめてください。",
            f"元の質問: {question}",
        ]
    )
    return "\n".join(lines)


def _build_data_agent_question(question: str) -> str:
    """Data Agent に対して、該当ゼロ時も代替集計を返すよう明示する。"""
    return "\n".join(
        [
            "あなたは Travel Marketing AI 用の Fabric Data Agent です。旅行販売データとレビューを使い、日本語でマーケティング分析を返してください。",
            "利用できるテーブルは travel_sales(Transaction_ID, Date, Travel_destination, Category, Schedule, Price, Price_per_person, Number_of_people, Age_group) と travel_review(Transaction_ID, Travel_destination, Rating, Emotions, Comments) だけです。",
            "最初に質問から Travel_destination、季節、顧客セグメント、カテゴリ、レビュー専用/売上専用/売上+レビューの分析種別を抽出し、抽出できた条件は必ず WHERE 条件または同等のフィルタに反映してください。沖縄、ハワイ、春、夏、ファミリー、学生などが明記されているのに全エリア・全年齢層・全カテゴリで回答してはいけません。",
            "厳密条件で売上またはレビューが取れない場合は、勝手に全体集計へ切り替えず、1) どの条件が0件か、2) 緩和した条件、3) 緩和後の実データを明示してください。",
            "条件緩和はユーザーに再指定を求めず自動で行ってください。例: 春+沖縄+ファミリーが0件なら、沖縄+春の全カテゴリ、沖縄+全季節+ファミリー、沖縄+全期間の順で実データを探し、取得できた近接データを表で示してください。",
            "売上、販売額、収益、予約数、取引数、人数、単価、旅行先別/カテゴリ別/年代別/季節別の分析は travel_sales を使ってください。",
            "レビュー、口コミ、評価、Rating、感情、Emotions、コメント、満足、不満、顧客の声に関する質問は travel_review を使ってください。",
            "売上とレビュー評価、カテゴリ別満足度、年代別満足度、高評価旅行先の売上など、両方が必要な質問は Transaction_ID で travel_sales と travel_review を結合してください。",
            "review-only の質問では travel_review を使い、Rating、Emotions、Comments、Travel_destination で回答してください。travel_sales に無い列が必要という理由で回答不能にしないでください。",
            "主要指標は、売上=SUM(Price)、予約数=COUNT(DISTINCT Transaction_ID)、旅行者数=SUM(Number_of_people)、平均取引額=AVG(Price)、1人あたり平均単価=AVG(Price_per_person)、平均評価=AVG(Rating)、レビュー件数=COUNT(*)、感情分布=Emotions ごとの COUNT と構成比です。",
            "Date は yyyy/MM/dd 形式の文字列として扱われる場合があります。月条件は /06/ /07/ /08/ のようなスラッシュ付き月、または日付として解釈できる方法で扱い、-06- のようなハイフン形式は使わないでください。",
            "春=3/4/5月、夏=6/7/8月、秋=9/10/11月、冬=12/1/2月、春休み=3/4月、ゴールデンウィーク=4月下旬から5月上旬、年末年始=12月下旬から1月上旬として扱ってください。年指定がない場合は利用可能な全期間を対象にし、その仮定を書いてください。",
            "Category は旅行カテゴリ/顧客カテゴリ/旅行タイプとして扱います。夏季/春季などの季節判定には絶対に使わず、季節は Date の月だけから判断してください。",
            "「ファミリー」「子連れ」「家族」「family」は Category の Family 表記ゆれを優先し、必要に応じて Number_of_people >= 3 または Age_group が 30代/40代の販売履歴、Comments に 子連れ/子ども/家族 を含むレビューとして扱ってください。",
            "「学生」は Age_group が 20代、または若年グループ旅行として Number_of_people >= 2 を優先し、「若年層」は Age_group が 20代/30代、「シニア」は 50代以上として扱ってください。",
            "Travel_destination は旅行先/目的地/観光地/地域/エリア/destination の同義語として扱い、日本語/英語/大文字小文字/部分一致の表記ゆれを考慮してください。",
            "売上上位は Travel_destination と必要なら Schedule/Category で集計し、Transaction_ID 単位に分解せず、SUM(Price)、COUNT(DISTINCT Transaction_ID)、SUM(Number_of_people) を返してください。",
            "「旅行先別」「destination別」「地域別」のランキングでは必ず Travel_destination で GROUP BY し、同じ旅行先を複数行に出してはいけません。取引単位の上位明細は、明細と明示された場合だけ返してください。",
            "レビュー評価は Travel_destination や Category で集計し、COUNT(*)、AVG(Rating)、Rating 分布、Emotions 分布、Comments の代表例を返してください。Comments に存在しない声やテーマは創作しないでください。",
            "前年同期比較は複数年の同月データがある場合だけ行い、足りない場合は利用可能な期間のトレンドや単年比較に切り替え、その制約を書いてください。",
            "厳密条件で0件または極端に少ない場合は、回答不能で終わらず、表記ゆれ、全年度の同月、関連する Category/Schedule などの順に条件を少し広げ、どの条件を広げたかを明示して近い実データを示してください。",
            "広告費、利益、原価、Web流入、天気、キャンペーン名など現在のスキーマにない項目は作らず、存在しない理由と売上/予約数/人数/レビュー評価での代替分析を提案してください。",
            "広告費やROIなど未知の指標を聞かれた場合も、提案だけで終わらず、旅行先別の売上、予約数、旅行者数、平均単価など実在列で代替ランキングを必ず作成してください。",
            "回答は 1. 結論、2. 使用条件、3. 主要指標、4. 表またはランキング、5. 補足 の順で簡潔にまとめてください。",
            "内部の GQL、GraphQL、JSON、クエリ実行トレース、ツール呼び出し詳細は出力せず、マーケティング担当者向けの分析結果だけを出力してください。",
            "必ず実データの数値を使い、X/XX/XXX、架空の例、プレースホルダー値は絶対に使わないでください。実データがない項目は「データなし」と書いてください。",
            "表を出す場合は実データの行だけを書いてください。「旅行先A」「○○件」「例のフォーマットです」などのテンプレート表は禁止です。",
            f"質問: {question}",
        ]
    )


def _extract_data_agent_tool_outputs(steps: object, *, max_outputs: int = 3) -> list[str]:
    """Data Agent の run steps から実クエリ結果だけを抽出する。"""
    outputs: list[str] = []
    for step in getattr(steps, "data", []) or []:
        step_details = getattr(step, "step_details", None)
        for tool_call in getattr(step_details, "tool_calls", []) or []:
            function = getattr(tool_call, "function", None)
            name = str(getattr(function, "name", "") or "")
            output = str(getattr(function, "output", "") or "").strip()
            if name not in _DATA_AGENT_RESULT_TOOL_NAMES or not output or output == "Loaded 0 fewshots":
                continue
            if output not in outputs:
                outputs.append(_safe_evidence_quote(output, max_length=1600))
            if len(outputs) >= max_outputs:
                return outputs
    return outputs


def _extract_region_filter(question: str) -> str | None:
    """質問文から既知の旅行先フィルタを抽出する。"""
    return next((destination for destination in _KNOWN_DESTINATIONS if destination in question), None)


def _extract_season_filter(question: str) -> str | None:
    """質問文から季節フィルタを抽出する。"""
    normalized = question.lower()
    season_terms = {
        "spring": ("春", "春休み", "spring"),
        "summer": ("夏", "夏休み", "summer"),
        "autumn": ("秋", "秋旅", "autumn", "fall"),
        "winter": ("冬", "冬休み", "winter"),
    }
    for season, terms in season_terms.items():
        if any(term in normalized for term in terms):
            return season
    return None


def _emit_evidence_event(
    tool_name: str,
    *,
    evidence: list[dict[str, object]],
    charts: list[dict[str, object]] | None = None,
) -> None:
    """ツール結果から生成した安全な根拠メタデータを追加送信する。"""
    emit_tool_event(
        build_tool_event_data(
            tool_name,
            "completed",
            agent_name="data-search-agent",
            source="local",
            provider="local",
            evidence=evidence,
            charts=charts,
        )
    )


def _sales_evidence(results: list[dict[str, Any]], *, source: str, season: str | None, region: str | None) -> list[dict[str, object]]:
    if not results:
        return [
            {
                "id": f"{source}-sales-empty",
                "title": "販売履歴検索",
                "source": source,
                "quote": "条件に一致する販売履歴は見つかりませんでした。",
                "retrieved_at": _utc_now_iso(),
                "metadata": {"rows": 0, "season": season or "", "region": region or ""},
            }
        ]
    total_revenue = sum(int(row.get("revenue") or 0) for row in results)
    total_bookings = sum(int(row.get("booking_count") or 0) for row in results)
    top = max(results, key=lambda row: int(row.get("revenue") or 0))
    return [
        {
            "id": f"{source}-sales-summary",
            "title": "販売履歴サマリ",
            "source": source,
            "quote": _safe_evidence_quote(
                f"{top.get('plan_name', '対象プラン')} が売上上位。合計売上 {total_revenue:,} 円、予約 {total_bookings} 件。"
            ),
            "relevance": 0.9,
            "retrieved_at": _utc_now_iso(),
            "metadata": {
                "rows": len(results),
                "season": season or "",
                "region": region or "",
                "top_destination": str(top.get("destination", "")),
            },
        }
    ]


def _sales_charts(results: list[dict[str, Any]], *, source: str) -> list[dict[str, object]]:
    if not results:
        return []
    rows = sorted(results, key=lambda row: int(row.get("revenue") or 0), reverse=True)[:5]
    return [
        {
            "chart_type": "bar",
            "title": "販売履歴 売上上位",
            "x_label": "プラン",
            "y_label": "売上",
            "series": ["revenue", "booking_count"],
            "data": [
                {
                    "plan": str(row.get("plan_name", ""))[:40],
                    "revenue": int(row.get("revenue") or 0),
                    "booking_count": int(row.get("booking_count") or 0),
                }
                for row in rows
            ],
            "metadata": {"source": source},
        }
    ]


def _review_evidence(results: list[dict[str, Any]], *, source: str, plan_name: str | None, min_rating: int | None) -> list[dict[str, object]]:
    if not results:
        return [
            {
                "id": f"{source}-reviews-empty",
                "title": "顧客レビュー検索",
                "source": source,
                "quote": "条件に一致する顧客レビューは見つかりませんでした。",
                "retrieved_at": _utc_now_iso(),
                "metadata": {"rows": 0, "plan_filter": plan_name or "", "min_rating": min_rating or 0},
            }
        ]
    top_reviews = sorted(results, key=lambda row: int(row.get("rating") or 0), reverse=True)[:3]
    return [
        {
            "id": f"{source}-review-{index + 1}",
            "title": str(row.get("plan_name", "顧客レビュー"))[:80],
            "source": source,
            "quote": _safe_evidence_quote(row.get("comment", "")),
            "relevance": min(max(float(row.get("rating") or 0) / 5, 0), 1),
            "retrieved_at": _utc_now_iso(),
            "metadata": {"rating": int(row.get("rating") or 0), "plan_filter": plan_name or "", "min_rating": min_rating or 0},
        }
        for index, row in enumerate(top_reviews)
    ]


def _review_charts(results: list[dict[str, Any]], *, source: str) -> list[dict[str, object]]:
    if not results:
        return []
    buckets: dict[int, int] = {}
    for row in results:
        rating = int(row.get("rating") or 0)
        buckets[rating] = buckets.get(rating, 0) + 1
    return [
        {
            "chart_type": "bar",
            "title": "顧客レビュー 評価分布",
            "x_label": "評価",
            "y_label": "件数",
            "series": ["count"],
            "data": [{"rating": f"{rating}★", "count": count} for rating, count in sorted(buckets.items())],
            "metadata": {"source": source},
        }
    ]


# --- Fabric Data Agent 連携 ---
# Fabric Data Agent の Published URL が設定されていれば、自然言語でデータ分析を実行する。
# Data Agent は NL2SQL で Lakehouse に問い合わせるため、SQL ハードコードが不要。


async def _query_data_agent(question: str) -> str | None:
    """Fabric Data Agent にクエリを送り、回答テキストを返す。

    Published URL が未設定、または通信エラーの場合は None を返す。
    `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` のときは v2 published URL と v2 用の
    短いプロンプトを使う。v1 のときは従来通り。
    """
    version = _resolve_data_agent_version()
    base_url = _resolve_data_agent_url(version)
    if not base_url:
        return None

    try:
        from src.agent_client import get_shared_credential

        credential = get_shared_credential()
        token = credential.get_token("https://analysis.windows.net/powerbi/api/.default")
    except (ValueError, OSError) as exc:
        logger.warning("Fabric Data Agent: トークン取得失敗: %s", exc)
        return None

    try:
        from openai import OpenAI

        # Fabric Data Agent は OpenAI Assistants API 互換エンドポイントを公開する。
        # SDK と同じ Fabric 固有ヘッダーを付与し、Published URL 直呼び出しでも
        # production Data Agent として扱われるようにする。
        activity_id = str(uuid.uuid4())
        client = OpenAI(
            base_url=base_url,
            api_key="",
            default_headers={
                "Authorization": f"Bearer {token.token}",
                "Accept": "application/json",
                "ActivityId": activity_id,
                "x-ms-workload-resource-moniker": activity_id,
                "x-ms-ai-assistant-scenario": "aiskill",
                "x-ms-ai-aiskill-stage": "production",
            },
            default_query={"api-version": "2024-05-01-preview"},
            timeout=_DATA_AGENT_POLL_TIMEOUT_SECONDS + 15,
        )

        # スレッド作成 → メッセージ送信 → 実行 → 結果取得
        # openai SDK は同期クライアントなので asyncio.to_thread で event loop ブロックを避ける
        import asyncio as _asyncio

        assistant = await _asyncio.to_thread(client.beta.assistants.create, model="not used")
        thread = await _asyncio.to_thread(client.beta.threads.create)
        question_payload = (
            _build_data_agent_question_v2(question) if version == "v2" else _build_data_agent_question(question)
        )
        await _asyncio.to_thread(
            client.beta.threads.messages.create,
            thread_id=thread.id,
            role="user",
            content=question_payload,
        )
        run = await _asyncio.to_thread(
            client.beta.threads.runs.create,
            thread_id=thread.id,
            assistant_id=assistant.id,
        )

        logger.info("Fabric Data Agent %s 経由で質問: %d 文字", version, len(question_payload))

        # ポーリング（最大 90 秒）。Fabric Data Agent は NL2SQL 生成と検証で
        # 60 秒を超えることがあるため、デモ中の不要な SQL 退避を避ける。
        import time as _time

        terminal_states = {"completed", "failed", "cancelled", "requires_action"}
        start = _time.time()
        while run.status not in terminal_states:
            if _time.time() - start > _DATA_AGENT_POLL_TIMEOUT_SECONDS:
                logger.warning("Fabric Data Agent: ポーリングタイムアウト (status=%s)", run.status)
                break
            run = await _asyncio.to_thread(
                client.beta.threads.runs.retrieve,
                thread_id=thread.id,
                run_id=run.id,
            )
            await _asyncio.sleep(2)

        if run.status != "completed":
            last_error = getattr(run, "last_error", None)
            logger.warning("Fabric Data Agent: run 失敗 (status=%s, last_error=%s)", run.status, last_error)
            # クリーンアップ
            try:
                await _asyncio.to_thread(client.beta.threads.delete, thread.id)
            except (ValueError, OSError):
                pass
            return None

        # 応答メッセージ取得（assistant メッセージごとに分割して保持）
        messages = await _asyncio.to_thread(client.beta.threads.messages.list, thread_id=thread.id, order="asc")
        assistant_messages: list[str] = []
        for msg in messages:
            if msg.role == "assistant":
                msg_text_parts: list[str] = []
                for content in msg.content:
                    if hasattr(content, "text"):
                        msg_text_parts.append(content.text.value)
                joined = "\n".join(msg_text_parts).strip()
                if joined:
                    assistant_messages.append(joined)
        tool_outputs: list[str] = []
        try:
            steps = await _asyncio.to_thread(
                client.beta.threads.runs.steps.list,
                thread_id=thread.id,
                run_id=run.id,
                order="asc",
            )
            tool_outputs = _extract_data_agent_tool_outputs(steps)
        except (AttributeError, ValueError, OSError) as exc:
            logger.warning("Fabric Data Agent: run steps 取得失敗: %s", exc)

        # クリーンアップ
        try:
            await _asyncio.to_thread(client.beta.threads.delete, thread.id)
        except (ValueError, OSError):
            pass

        # 複数の assistant メッセージがある場合は最終メッセージを優先する。
        # Data Agent は self-retry の過程で「技術的なエラーが発生したので分解します」のような
        # 中間ステータスメッセージを出すことがあるため、全結合すると最終回答が成功でも
        # 強い失敗フレーズで低信頼判定されてしまう。最終メッセージが高信頼ならそれを採用する。
        answer = _select_data_agent_answer(assistant_messages)
        if answer and tool_outputs and _is_low_confidence_data_agent_answer(answer):
            tool_answer = "\n\n".join(tool_outputs)
            if not _is_low_confidence_data_agent_answer(tool_answer):
                answer = (
                    "Fabric Data Agent の最終回答が十分な実数を含まなかったため、"
                    "Data Agent の実行結果を根拠として返します。\n"
                    f"{tool_answer}"
                )
        if answer:
            logger.info("Fabric Data Agent から回答取得: %d 文字", len(answer))
            return answer
        return None

    except (ImportError, ValueError, OSError) as exc:
        logger.warning("Fabric Data Agent 呼び出しに失敗: %s", exc)
        return None
    except Exception as exc:
        logger.warning("Fabric Data Agent で予期しないエラー: %s", exc)
        return None


# --- Code Interpreter 自動検出 ---
# None = 未テスト、True = 利用可能、False = 利用不可（404 等で失敗済み）
_code_interpreter_available: bool | None = None


def set_code_interpreter_available(available: bool) -> None:
    """Code Interpreter の利用可能状態を設定する（実行時自動検出用）。"""
    global _code_interpreter_available
    _code_interpreter_available = available
    logger.info("Code Interpreter 利用可能状態を %s に設定", available)


def _should_enable_code_interpreter() -> bool:
    """Code Interpreter を有効にすべきかを判定する。

    判定ロジック:
    1. ENABLE_CODE_INTERPRETER=false で明示的に無効化 → False
    2. 実行時に 404 等で失敗済み → False
    3. それ以外 → True（初回は有効化して試す）
    """
    env_val = os.environ.get("ENABLE_CODE_INTERPRETER", "").lower()
    if env_val in ("false", "0", "no"):
        return False
    if _code_interpreter_available is False:
        return False
    return True


# --- ツール出力スキーマ（バリデーション・テスト用） ---


class SalesRecord(BaseModel):
    """販売履歴の集約レコード"""

    plan_name: str
    destination: str
    season: str
    revenue: int
    pax: int
    customer_segment: str
    booking_count: int


class CustomerReview(BaseModel):
    """顧客レビューレコード"""

    plan_name: str
    rating: int
    comment: str


# --- Fabric Lakehouse SQL 接続 ---

# Azure SQL / Fabric 用のトークンスコープ
_SQL_TOKEN_SCOPE = "https://database.windows.net/.default"


def _query_fabric(query: str, params: list | None = None) -> list[dict]:
    """Fabric Lakehouse SQL エンドポイントにクエリを実行し、結果を辞書リストで返す。

    接続に失敗した場合や pyodbc 未インストール時は空リストを返す。
    """
    if not _HAS_PYODBC:
        logger.debug("pyodbc が未インストールのため Fabric SQL 接続をスキップ")
        return []

    settings = get_settings()
    endpoint = settings.get("fabric_sql_endpoint", "")
    if not endpoint:
        return []
    database = settings.get("fabric_lakehouse_database", "Travel_Lakehouse") or "Travel_Lakehouse"

    try:
        credential = DefaultAzureCredential()
        token = credential.get_token(_SQL_TOKEN_SCOPE)
        token_bytes = token.token.encode("utf-16-le")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

        # SQL_COPT_SS_ACCESS_TOKEN = 1256
        conn = pyodbc.connect(
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={endpoint};"
            f"Database={database};"
            f"Encrypt=yes;"
            f"TrustServerCertificate=no",
            attrs_before={1256: token_struct},
        )

        cursor = conn.cursor()
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        columns = [desc[0] for desc in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

        cursor.close()
        conn.close()
        logger.info("Fabric SQL クエリ成功: %d 行取得", len(rows))
        return rows

    except (pyodbc.Error, ValueError, OSError, ClientAuthenticationError, CredentialUnavailableError) as exc:
        logger.warning("Fabric SQL 接続エラー（CSV にフォールバック）: %s", exc)
        return []


def _fabric_table_name(setting_key: str, default_name: str) -> str:
    """Fabric table 名を環境設定から安全な SQL identifier として解決する。"""
    value = str(get_settings().get(setting_key, "") or default_name).strip()
    if not _SQL_IDENTIFIER_PATTERN.fullmatch(value):
        logger.warning("Fabric table 名が不正なため既定値を使います: %s", setting_key)
        return default_name
    return value


def _fabric_table_lookup_name(table_name: str) -> str:
    """INFORMATION_SCHEMA で参照する table 名を取得する。"""
    return table_name.rsplit(".", 1)[-1]


def _fabric_table_columns(table_name: str) -> set[str]:
    """Fabric table の列名を小文字化して取得する。"""
    rows = _query_fabric(
        "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?",
        [_fabric_table_lookup_name(table_name)],
    )
    return {str(row.get("COLUMN_NAME", "")).lower() for row in rows}


# --- デモデータ読み込み（Fabric Lakehouse 未接続時は CSV から読み込む） ---

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_csv(filename: str) -> list[dict]:
    """CSV ファイルからデータを読み込む"""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _get_sales_data_from_fabric(
    season: str | None = None,
    region: str | None = None,
) -> list[dict]:
    """Fabric Lakehouse の sales_results テーブルから集約データを取得する。

    SQL 側で季節・地域フィルタと集約を実行し、結果を返す。
    取得できなかった場合は空リストを返す。
    """
    sales_table = _fabric_table_name("fabric_sales_table", "sales_results")
    table_columns = _fabric_table_columns(sales_table)
    is_v2_schema = {
        "destination_region",
        "departure_date",
        "total_revenue_jpy",
        "pax",
    }.issubset(table_columns)
    is_ws3iq_schema = (not is_v2_schema) and {
        "travel_destination",
        "date",
        "price",
        "number_of_people",
        "age_group",
    }.issubset(table_columns)

    where_clauses: list[str] = []
    params: list = []

    if region:
        if is_v2_schema:
            where_clauses.append("(destination_region LIKE ? OR destination_country LIKE ?)")
            params.extend([f"%{region}%", f"%{region}%"])
        elif is_ws3iq_schema:
            where_clauses.append("Travel_destination LIKE ?")
            params.append(f"%{region}%")
        else:
            where_clauses.append("destination LIKE ?")
            params.append(f"%{region}%")

    if season:
        season_months: dict[str, tuple[int, ...]] = {
            "spring": (3, 4, 5),
            "summer": (6, 7, 8),
            "autumn": (9, 10, 11),
            "winter": (12, 1, 2),
        }
        months = season_months.get(season)
        if months:
            placeholders = ", ".join("?" for _ in months)
            if is_v2_schema:
                date_expr = "departure_date"
            elif is_ws3iq_schema:
                date_expr = "TRY_CONVERT(date, [Date], 111)"
            else:
                date_expr = "departure_date"
            where_clauses.append(f"MONTH({date_expr}) IN ({placeholders})")
            params.extend(months)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if is_v2_schema:
        # Phase 9 v2 schema: lh_travel_marketing_v2.dbo.booking
        # 列: destination_region / destination_country / departure_date / season /
        #     total_revenue_jpy / pax / product_type / customer_id
        query = f"""
            SELECT
                CONCAT(destination_region, ' ', COALESCE(product_type, '')) AS plan_name,
                destination_region AS destination,
                CASE
                    WHEN MONTH(departure_date) IN (3, 4, 5) THEN 'spring'
                    WHEN MONTH(departure_date) IN (6, 7, 8) THEN 'summer'
                    WHEN MONTH(departure_date) IN (9, 10, 11) THEN 'autumn'
                    ELSE 'winter'
                END AS season,
                SUM(CAST(total_revenue_jpy AS BIGINT)) AS revenue,
                SUM(CAST(pax AS INT)) AS pax,
                MIN(COALESCE(product_type, '')) AS customer_segment,
                COUNT(*) AS booking_count
            FROM {sales_table}
            {where_sql}
            GROUP BY
                destination_region,
                product_type,
                CASE
                    WHEN MONTH(departure_date) IN (3, 4, 5) THEN 'spring'
                    WHEN MONTH(departure_date) IN (6, 7, 8) THEN 'summer'
                    WHEN MONTH(departure_date) IN (9, 10, 11) THEN 'autumn'
                    ELSE 'winter'
                END
        """
    elif is_ws3iq_schema:
        date_expr = "TRY_CONVERT(date, [Date], 111)"
        season_expr = f"""
            CASE
                WHEN MONTH({date_expr}) IN (3, 4, 5) THEN 'spring'
                WHEN MONTH({date_expr}) IN (6, 7, 8) THEN 'summer'
                WHEN MONTH({date_expr}) IN (9, 10, 11) THEN 'autumn'
                ELSE 'winter'
            END
        """
        query = f"""
            SELECT
                CONCAT(Travel_destination, ' ', Schedule) AS plan_name,
                Travel_destination AS destination,
                {season_expr} AS season,
                SUM(CAST(Price AS BIGINT)) AS revenue,
                SUM(CAST(Number_of_people AS INT)) AS pax,
                MIN(Age_group) AS customer_segment,
                COUNT(*) AS booking_count
            FROM {sales_table}
            {where_sql}
            GROUP BY
                Travel_destination,
                Schedule,
                {season_expr}
        """
    else:
        query = f"""
            SELECT
                plan_name,
                destination,
                CASE
                    WHEN MONTH(departure_date) IN (3, 4, 5) THEN 'spring'
                    WHEN MONTH(departure_date) IN (6, 7, 8) THEN 'summer'
                    WHEN MONTH(departure_date) IN (9, 10, 11) THEN 'autumn'
                    ELSE 'winter'
                END AS season,
                SUM(CAST(revenue AS BIGINT)) AS revenue,
                SUM(CAST(pax AS INT)) AS pax,
                MIN(customer_segment) AS customer_segment,
                COUNT(*) AS booking_count
            FROM {sales_table}
            {where_sql}
            GROUP BY
                plan_name,
                destination,
                CASE
                    WHEN MONTH(departure_date) IN (3, 4, 5) THEN 'spring'
                    WHEN MONTH(departure_date) IN (6, 7, 8) THEN 'summer'
                    WHEN MONTH(departure_date) IN (9, 10, 11) THEN 'autumn'
                    ELSE 'winter'
                END
        """

    return _query_fabric(query, params if params else None)


def _get_reviews_from_fabric(
    plan_name: str | None = None,
    min_rating: int | None = None,
) -> list[dict]:
    """Fabric Lakehouse の customer_reviews テーブルからレビューを取得する。

    取得できなかった場合は空リストを返す。
    """
    reviews_table = _fabric_table_name("fabric_reviews_table", "customer_reviews")
    table_columns = _fabric_table_columns(reviews_table)
    is_v2_schema = {"rating", "comment"}.issubset(table_columns) and (
        "destination_region" in table_columns or "booking_id" in table_columns
    )
    is_ws3iq_schema = (not is_v2_schema) and {"travel_destination", "rating", "comments"}.issubset(table_columns)

    where_clauses: list[str] = []
    params: list = []

    if plan_name:
        if is_v2_schema:
            where_clauses.append("(destination_region LIKE ? OR plan_name LIKE ?)")
            params.extend([f"%{plan_name}%", f"%{plan_name}%"])
        elif is_ws3iq_schema:
            where_clauses.append("Travel_destination LIKE ?")
            params.append(f"%{plan_name}%")
        else:
            where_clauses.append("plan_name LIKE ?")
            params.append(f"%{plan_name}%")

    if min_rating is not None:
        where_clauses.append("Rating >= ?" if is_ws3iq_schema else "rating >= ?")
        params.append(min_rating)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if is_v2_schema:
        # Phase 9 v2 schema: lh_travel_marketing_v2.dbo.tour_review
        # 列: rating / comment / destination_region / plan_name / review_date / booking_id
        query = f"""
            SELECT
                COALESCE(plan_name, destination_region, '旅行プラン') AS plan_name,
                rating AS rating,
                comment AS comment
            FROM {reviews_table}
            {where_sql}
            ORDER BY review_date DESC
        """
    elif is_ws3iq_schema:
        query = f"""
            SELECT
                Travel_destination AS plan_name,
                Rating AS rating,
                Comments AS comment
            FROM {reviews_table}
            {where_sql}
            ORDER BY Transaction_ID DESC
        """
    else:
        query = f"""
            SELECT plan_name, rating, comment
            FROM {reviews_table}
            {where_sql}
            ORDER BY review_date DESC
        """

    return _query_fabric(query, params if params else None)


def _build_fabric_sql_analysis(question: str) -> str | None:
    """Data Agent が使えない場合に Fabric SQL から分析要約を生成する。"""
    region = _extract_region_filter(question)
    season = _extract_season_filter(question)
    sales = _get_sales_data_from_fabric(season=season, region=region)
    reviews = _get_reviews_from_fabric(plan_name=region)
    broadened = False
    if not sales and (season or region):
        sales = _get_sales_data_from_fabric(region=region)
        broadened = True
    if not reviews and region:
        reviews = _get_reviews_from_fabric()
        broadened = True
    if not sales and not reviews:
        return None

    top_sales = sorted(sales, key=lambda row: int(row.get("revenue") or 0), reverse=True)[:5]
    lines = [
        "ws-3iq-demo Lakehouse の SQL endpoint から実データを集計しました。",
        f"質問: {question}",
    ]
    filters = [f"地域={region}" if region else "", f"季節={season}" if season else ""]
    filters = [value for value in filters if value]
    if filters:
        lines.append(f"適用フィルタ: {', '.join(filters)}")
    if broadened:
        lines.append("注: 厳密条件のデータが少ないため、一部は条件を広げて補強しました。")
    if top_sales:
        lines.append("売上上位:")
        for row in top_sales:
            lines.append(
                "- "
                f"{row.get('plan_name', row.get('destination', '旅行プラン'))}: "
                f"売上 {int(row.get('revenue') or 0):,} 円 / "
                f"人数 {int(row.get('pax') or 0):,} 名 / "
                f"予約 {int(row.get('booking_count') or 0):,} 件"
            )
    if reviews:
        avg_rating = sum(int(row.get("rating") or 0) for row in reviews) / len(reviews)
        lines.append(f"レビュー件数: {len(reviews)} 件、平均評価: {avg_rating:.1f}")
        for row in reviews[:3]:
            lines.append(f"- {row.get('plan_name', '旅行先')} ({row.get('rating', '-')}/5): {row.get('comment', '')}")
    return "\n".join(lines)


def _get_sales_data() -> list[dict]:
    """販売履歴データを取得する（CSV → 集約済みサマリ）"""
    rows = _load_csv("sales_history.csv")
    if not rows:
        return _FALLBACK_SALES
    # プラン×目的地×季節で集約
    agg: dict[str, dict] = {}
    for r in rows:
        key = r["plan_name"]
        if key not in agg:
            season = ""
            dest = r.get("destination", "")
            # departure_date から季節を推定
            dep = r.get("departure_date", "")
            if dep:
                month = int(dep.split("-")[1]) if "-" in dep else 0
                if month in (3, 4, 5):
                    season = "spring"
                elif month in (6, 7, 8):
                    season = "summer"
                elif month in (9, 10, 11):
                    season = "autumn"
                else:
                    season = "winter"
            agg[key] = {
                "plan_name": key,
                "destination": dest,
                "season": season,
                "revenue": 0,
                "pax": 0,
                "customer_segment": r.get("customer_segment", ""),
                "booking_count": 0,
            }
        agg[key]["revenue"] += int(r.get("revenue", 0))
        agg[key]["pax"] += int(r.get("pax", 0))
        agg[key]["booking_count"] += 1
    return list(agg.values())


def _get_reviews() -> list[dict]:
    """顧客レビューデータを取得する"""
    rows = _load_csv("customer_reviews.csv")
    if not rows:
        return _FALLBACK_REVIEWS
    return [
        {
            "plan_name": r["plan_name"],
            "rating": int(r["rating"]),
            "comment": r["comment"],
        }
        for r in rows
    ]


# フォールバック用の最小データ
_FALLBACK_SALES = [
    {
        "plan_name": "沖縄3泊4日ファミリープラン",
        "destination": "沖縄",
        "season": "spring",
        "revenue": 358400,
        "pax": 4,
        "customer_segment": "ファミリー",
        "booking_count": 45,
    },
]

_FALLBACK_REVIEWS = [
    {"plan_name": "沖縄3泊4日ファミリープラン", "rating": 5, "comment": "子どもが大喜びでした。美ら海水族館が最高！"},
]


# --- ツール定義 ---


@tool
async def query_data_agent(question: str) -> str:
    """Fabric Data Agent に自然言語でデータ分析を依頼する。

    Fabric Data Agent が Lakehouse のデータを自動で SQL に変換して実行し、
    分析結果を返す。複雑なデータ分析やクロス集計に適している。

    **MUST: ユーザが指定した季節 / 旅行先 / 顧客セグメント / 年代 / 予算等の固有表現は、
    ユーザの言葉のまま `question` に保持してください。**
    DO NOT 「ユーザー指示は不明瞭ですが」「基礎分析として」のような前置きを付けない。
    DO NOT 季節 / 旅行先 / 顧客セグメントを「年代・家族構成・旅行動機」のような
    総称に置き換えない (Data Agent が grounded data を返せなくなり、結果として
    SQL fallback に逃げて UI のデモ品質が低下します)。

    例:
    - DO: 「夏のハワイ学生旅行向けの売上・予約数・旅行者数・平均評価を教えてください」
      (ユーザが「夏のハワイ学生旅行向けプランを企画して」と言った場合)
    - DON'T: 「ユーザー指示は不明瞭ですが、旅行プラン企画のための基礎分析として、
      売上履歴と顧客レビューから主要なターゲット、季節別・地域別の売上トレンド…」

    Args:
        question: データに関する質問。ユーザの固有表現 (季節 / 旅行先 / 顧客セグメント等)
            を保持した具体的な聞き方で渡してください。
    """
    async with trace_tool_invocation("query_data_agent", agent_name="data-search-agent"):
        runtime = _resolve_fabric_data_agent_runtime()

        # rubber-duck `prompt-preserve-impl-review` 2026-05-02 BLOCKING #1:
        # 1 回目の DA 呼び出し前に、LLM が rewrite した `question` から
        # ユーザの explicit filters (夏 / ハワイ / 学生 等) が drop されているかを
        # 検査し、drop されていれば元プロンプト由来の filter を復元する。
        # これをやらないと、DA が「rewrite された vague prompt」に対して
        # broad grounded answer (低信頼判定にならない) を返してしまったときに
        # 誤ったコホートのデータが UI に表示されるリスクがある。
        #
        # 復元は **追加のみ** (additive): 元プロンプトを prefix として prepend し、
        # rewritten question はそのまま保持する。これにより LLM が "売上・予約数・
        # 旅行者数を教えて" のような elaboration を加えていても、その情報を失わない。
        original_prompt = _get_original_user_prompt()
        first_call_question = question
        first_call_reconciled = False
        if original_prompt and runtime == "rest" and _resolve_data_agent_version() == "v2":
            original_filters = _extract_normalized_filters(original_prompt) or {}
            rewritten_filters = _extract_normalized_filters(question) or {}
            missing_filters = {
                key: value for key, value in original_filters.items() if key not in rewritten_filters
            }
            if missing_filters:
                # 元プロンプトの言葉を verbatim で先頭に置き、rewritten question を後段に残す。
                # DA は「ユーザの実際の言葉」と「LLM の elaboration」を両方読める。
                first_call_question = (
                    f"ユーザの実際の質問: 「{original_prompt}」\n\n"
                    f"このユーザ質問に対して Lakehouse データから具体的な数値で回答してください。"
                    f"\n\n参考: {question}"
                )
                first_call_reconciled = True
                logger.info(
                    "Fabric Data Agent: 1回目 prompt 復元 missing_filters=%s "
                    "original_filters=%s rewritten_filters=%s",
                    list(missing_filters.keys()),
                    list(original_filters.keys()),
                    list(rewritten_filters.keys()),
                )

        result = await _query_data_agent(first_call_question) if runtime == "rest" else None
        attempt_label = "first_reconciled" if first_call_reconciled else "first"
        retry_attempted = False
        # 1 回目が低信頼 (Fabric Data Agent が NL2Ontology で 3 条件以上 conjoined
        # filter を取りこぼした「実データの取得ができませんでした」型の応答) のとき、
        # 日本語の segment / season を canonical 英語値に正規化した structured prompt
        # で 1 回だけ retry する。これで Phase 10 P02 / P07 系の no_data 失敗を
        # 救い、SQL fallback で「Data Agent が動いた」演出をするのではなく
        # Data Agent 本体に正しいデータを返させる (rubber-duck 監査 2026-05-02)。
        #
        # ★ v2 (Travel_Ontology_DA_v2) 限定: structured retry prompt は v2 lakehouse
        # スキーマの `customer_segment` / `season` (英語小文字) を直書きするので、
        # v1 (travel_sales: Category / Age_group) には合わない。v1 では retry をスキップする。
        if (
            runtime == "rest"
            and result
            and _is_low_confidence_data_agent_answer(result)
            and _resolve_data_agent_version() == "v2"
        ):
            # rubber-duck `agent1-da-prompt-preserve` 2026-05-02 BLOCKING #2:
            # LLM が rewrite した `question` には「家族構成」のような誤マッピング
            # 候補が含まれることがあるため、ユーザの **元プロンプト** から filters を
            # 抽出する。元プロンプトが利用可能な場合のみ使い、ない場合 (test 等) は
            # 既存挙動どおり tool 引数 `question` をそのまま使う。
            filter_source = _get_original_user_prompt() or question
            normalized_filters = _extract_normalized_filters(filter_source)
            if normalized_filters:
                retry_attempted = True
                retry_prompt = _build_structured_retry_question(filter_source, normalized_filters)
                logger.info(
                    "Fabric Data Agent: 1回目低信頼 → structured retry filters=%s source=%s",
                    normalized_filters,
                    "original_user_prompt" if _get_original_user_prompt() else "tool_argument",
                )
                retry_result = await _query_data_agent(retry_prompt)
                if retry_result and not _is_low_confidence_data_agent_answer(retry_result):
                    logger.info(
                        "Fabric Data Agent: structured_retry succeeded filters=%s answer_len=%d",
                        normalized_filters,
                        len(retry_result),
                    )
                    result = retry_result
                    attempt_label = "structured_retry"
                else:
                    logger.info(
                        "Fabric Data Agent: structured_retry も低信頼 → SQL fallback へ filters=%s",
                        normalized_filters,
                    )
        if result and not _is_low_confidence_data_agent_answer(result):
            _emit_evidence_event(
                "query_data_agent",
                evidence=[
                    {
                        "id": "fabric-data-agent-answer",
                        "title": "Fabric Data Agent 回答",
                        "source": "fabric",
                        "quote": _safe_evidence_quote(result),
                        "relevance": 0.85,
                        "retrieved_at": _utc_now_iso(),
                        "metadata": {"runtime": "fabric_data_agent", "attempt": attempt_label},
                    }
                ],
            )
            return json.dumps(
                {"source": "Fabric Data Agent", "answer": result, "attempt": attempt_label},
                ensure_ascii=False,
            )
        fabric_sql_answer = _build_fabric_sql_analysis(question)
        if fabric_sql_answer:
            answer = fabric_sql_answer
            source = "Fabric SQL primary" if runtime != "rest" else "Fabric SQL fallback"
            metadata = {
                "runtime": "fabric_sql_primary" if runtime != "rest" else "fabric_sql_fallback",
                "data_agent_rest": "disabled" if runtime != "rest" else "unavailable",
            }
            title = "Fabric SQL 分析" if runtime != "rest" else "Fabric SQL フォールバック"
            relevance = 0.88 if runtime != "rest" else 0.75
            if result:
                answer = fabric_sql_answer
                source = "Fabric SQL"
                metadata = {
                    "runtime": "fabric_sql_supplement",
                    "data_agent_quality": "low_confidence",
                    "structured_retry_attempted": retry_attempted,
                }
                title = "Fabric Lakehouse 集計"
                relevance = 0.9
            elif runtime != "rest":
                answer = fabric_sql_answer
            _emit_evidence_event(
                "query_data_agent",
                evidence=[
                    {
                        "id": "fabric-sql-data-agent-fallback",
                        "title": title,
                        "source": "fabric",
                        "quote": _safe_evidence_quote(answer),
                        "relevance": relevance,
                        "retrieved_at": _utc_now_iso(),
                        "metadata": metadata,
                    }
                ],
            )
            return json.dumps(
                {"source": source, "answer": answer},
                ensure_ascii=False,
            )
        _emit_evidence_event(
            "query_data_agent",
            evidence=[
                {
                    "id": "fabric-data-agent-unavailable",
                    "title": "Fabric Data Agent フォールバック",
                    "source": "local",
                    "quote": "Fabric Data Agent が利用できないため、ローカル検索ツールへのフォールバックを案内しました。",
                    "retrieved_at": _utc_now_iso(),
                    "metadata": {"runtime": "fallback"},
                }
            ],
        )
        return json.dumps(
            {
                "source": "fallback",
                "message": "Fabric Data Agent は現在利用できません。search_sales_history / search_customer_reviews をお使いください。",
            },
            ensure_ascii=False,
        )


@tool
async def search_sales_history(
    query: str,
    season: str | None = None,
    region: str | None = None,
) -> str:
    """Fabric Lakehouse の sales_history を検索する。

    Args:
        query: 検索クエリ（例: 「沖縄の春季売上」）
        season: 季節フィルタ（spring/summer/autumn/winter）
        region: 地域フィルタ（例: 「沖縄」「北海道」）
    """
    async with trace_tool_invocation("search_sales_history", agent_name="data-search-agent"):
        # Fabric SQL を優先し、取得できなければ CSV にフォールバック
        results = _get_sales_data_from_fabric(season=season, region=region)
        if results:
            logger.info("Fabric SQL から販売データ %d 件取得", len(results))
            _emit_evidence_event(
                "search_sales_history",
                evidence=_sales_evidence(results, source="fabric", season=season, region=region),
                charts=_sales_charts(results, source="fabric"),
            )
            return json.dumps(results, ensure_ascii=False, default=str)

        logger.info("CSV フォールバックで販売データを取得")
        results = _get_sales_data()
        if season:
            results = [r for r in results if r["season"] == season]
        if region:
            results = [r for r in results if region in r["destination"]]
        _emit_evidence_event(
            "search_sales_history",
            evidence=_sales_evidence(results, source="local", season=season, region=region),
            charts=_sales_charts(results, source="local"),
        )
        return json.dumps(results, ensure_ascii=False)


@tool
async def search_customer_reviews(
    plan_name: str | None = None,
    min_rating: int | None = None,
) -> str:
    """顧客レビューを検索する。

    Args:
        plan_name: プラン名でフィルタ
        min_rating: 最低評価でフィルタ（1〜5）
    """
    async with trace_tool_invocation("search_customer_reviews", agent_name="data-search-agent"):
        # Fabric SQL を優先し、取得できなければ CSV にフォールバック
        results = _get_reviews_from_fabric(plan_name=plan_name, min_rating=min_rating)
        if results:
            logger.info("Fabric SQL からレビュー %d 件取得", len(results))
            _emit_evidence_event(
                "search_customer_reviews",
                evidence=_review_evidence(results, source="fabric", plan_name=plan_name, min_rating=min_rating),
                charts=_review_charts(results, source="fabric"),
            )
            return json.dumps(results, ensure_ascii=False, default=str)

        logger.info("CSV フォールバックでレビューデータを取得")
        results = _get_reviews()
        if plan_name:
            results = [r for r in results if plan_name in r["plan_name"]]
        if min_rating is not None:
            results = [r for r in results if r["rating"] >= min_rating]
        _emit_evidence_event(
            "search_customer_reviews",
            evidence=_review_evidence(results, source="local", plan_name=plan_name, min_rating=min_rating),
            charts=_review_charts(results, source="local"),
        )
        return json.dumps(results, ensure_ascii=False)


# --- エージェント作成 ---

INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **データ分析エージェント** です。

## パイプライン全体の流れ
1. **データ分析（あなた）**: 売上データ・顧客レビューを分析し、ターゲット・トレンド・改善点を抽出
2. **施策立案**: あなたの分析結果をもとにマーケティング企画書を作成
3. **承認ステップ**: ユーザーが企画書を確認・承認
4. **規制チェック**: 企画書の法令・規制チェック
5. **販促物生成**: 販促物（ブローシャ・画像）を生成

## あなたの役割
ユーザーの指示からターゲット・季節・地域・予算等を抽出し、
販売履歴と顧客レビューを検索・分析して、次の施策立案工程のための基礎データを提供します。

## 入力
ユーザーからの自然言語指示（例: 「春の北海道旅行プランを企画して」）

## 出力フォーマット（Markdown）
1. **ターゲット分析**: 抽出したターゲット情報（年代・家族構成・旅行動機）
2. **売上トレンド**: 前年比・セグメント比率・季節別傾向
3. **顧客評価**: 人気ポイント・不満点（レビューの引用を含む）
4. **推奨事項**: データに基づく施策の方向性

## ツール使用ルール
- `query_data_agent` が利用可能な場合は**まずそちらを使ってください**（Fabric Data Agent が自然言語で Lakehouse を分析）
- `query_data_agent` が利用できない場合、または追加データが必要な場合は `search_sales_history` と `search_customer_reviews` を使ってください
- データが見つからない場合でも、利用可能なデータから最善の分析を行ってください
- 分析結果は具体的な数値を含めてください（売上額、件数、評価スコア等）

## query_data_agent への質問の作り方（**最重要**）
`query_data_agent` の `question` 引数は **ユーザの言葉をそのまま保持**してください。
LLM が独自に言い換えると Fabric Data Agent が grounded data を返せなくなります。

### MUST
- ユーザが書いた季節（春/夏/秋/冬）、旅行先（ハワイ/沖縄/京都 等）、顧客セグメント（学生/ファミリー/シニア/カップル 等）、年代、予算は **必ず原文のまま** `question` に含める
- 「売上・予約数・旅行者数・平均評価を教えてください」のように、欲しい指標を具体的に並べる

### DO NOT
- 「ユーザー指示は不明瞭ですが」「基礎分析として」のような前置きを付けない
- 「学生」を「年代・家族構成・旅行動機」のような抽象的な総称に置き換えない
- 「ハワイ」を「リゾート系」「海外旅行先」に一般化しない
- 「夏」を「季節別」に置き換えない

### 例
- ユーザ入力: 「夏のハワイ学生旅行向けプランを企画して」
  - DO: `query_data_agent("夏のハワイ学生旅行向けの売上・予約数・旅行者数・平均評価を教えてください")`
  - DON'T: `query_data_agent("ユーザー指示は不明瞭ですが、旅行プラン企画のための基礎分析として、売上履歴と顧客レビューから主要なターゲット（年代・家族構成・旅行動機）、季節別・地域別の売上トレンドを…")`
- ユーザ入力: 「春の沖縄ファミリープランを考えたい」
  - DO: `query_data_agent("春の沖縄ファミリー向けプランの売上・予約数・平均単価・顧客評価を教えてください")`
  - DON'T: 「沖縄プランの傾向を教えて」(ファミリー条件が消えている)

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
- **存在しないリンク・ダウンロード・PowerPoint 出力・グラフ画像の参照は絶対に書かないでください** (実際にダウンロードできないため UX を損ねる)。例: 「売上分析グラフをダウンロード」「PowerPoint で出力可能」「[グラフを開く](url)」等は禁止
"""

_CODE_INTERPRETER_INSTRUCTION_SUFFIX = """
## データ可視化
売上データを分析した後、Code Interpreter を使って以下の可視化を生成してください:
- 売上推移の折れ線グラフまたは棒グラフ
- 顧客セグメント別の円グラフ
グラフは日本語ラベルで作成し、見やすい色使いにしてください。
"""


def create_data_search_agent(model_settings: dict | None = None):
    """データ検索エージェントを作成する。

    Code Interpreter はリージョン依存（East US 2 / Sweden Central 推奨）。
    利用できない場合はテキスト分析のみで動作する。

    Code Interpreter の有効化:
    - デフォルト: 有効（初回実行時に 404 が発生すると自動的に無効化）
    - 明示的に無効化: ENABLE_CODE_INTERPRETER=false
    """
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

    agent_tools: list = [query_data_agent, search_sales_history, search_customer_reviews]
    instructions = INSTRUCTIONS

    enable_ci = _should_enable_code_interpreter()
    if enable_ci:
        code_interpreter_tool = client.get_code_interpreter_tool()
        agent_tools.append(code_interpreter_tool)
        instructions = INSTRUCTIONS + _CODE_INTERPRETER_INSTRUCTION_SUFFIX
        logger.info("Code Interpreter を有効化してエージェントを作成")
    else:
        reason = (
            "環境変数で無効化"
            if os.environ.get("ENABLE_CODE_INTERPRETER", "").lower() in ("false", "0", "no")
            else "実行時エラーにより無効化"
        )
        logger.info("Code Interpreter なしでエージェントを作成（%s）", reason)

    agent_kwargs: dict = {
        "name": "data-search-agent",
        "instructions": instructions,
        "tools": agent_tools,
    }
    default_opts: dict = {"max_output_tokens": 16384}
    if model_settings:
        if "temperature" in model_settings:
            default_opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            default_opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            default_opts["top_p"] = model_settings["top_p"]
    agent_kwargs["default_options"] = default_opts
    return client.as_agent(**agent_kwargs)
