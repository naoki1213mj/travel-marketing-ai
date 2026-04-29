"""Agent1: データ検索エージェント。Fabric Lakehouse から販売・顧客データを検索・分析する。"""

import csv
import json
import logging
import os
import re
import struct
import uuid
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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_evidence_quote(value: object, *, max_length: int = 220) -> str:
    normalized = redact_sensitive_text(str(value or "").replace("\n", " ").strip())
    return f"{normalized[: max_length - 1]}…" if len(normalized) > max_length else normalized


_LOW_CONFIDENCE_DATA_AGENT_PATTERNS = (
    "分析を実行できませんでした",
    "抽出できませんでした",
    "データ未表示",
    "提示できません",
    "表示できません",
    "見つかりませんでした",
    "見つかりません",
    "存在しませんでした",
    "利用可能なデータが無い",
    "確認できませんでした",
    "取得できません",
    "取得できませんでした",
    "技術的な理由",
    "内部エラー",
    "問題が発生",
    "クエリを実行しましたが",
    "条件に一致",
    "追加提示してください",
    "必要であれば",
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
    "数値・内容例",
    "具体例です",
    "分析例",
)
_MISSING_SALES_DATA_AGENT_PATTERNS = (
    "売上実績",
    "売上上位",
    "合計売上",
    "予約数",
    "予約件数",
    "合計人数",
)
_DATA_AGENT_RESULT_TOOL_NAMES = {
    "trace.analyze_ontology",
    "analyze.database.execute",
}
_DATA_AGENT_POLL_TIMEOUT_SECONDS = 90
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


def _is_low_confidence_data_agent_answer(answer: str) -> bool:
    """Data Agent が接続成功でも実質的に分析できていない回答を検出する。"""
    normalized = answer.lower()
    if not normalized.strip():
        return True
    if any(pattern in normalized for pattern in _PLACEHOLDER_DATA_AGENT_PATTERNS):
        return True
    if any(pattern in answer for pattern in _MISSING_SALES_DATA_AGENT_PATTERNS) and (
        "データなし" in answer
        or "データ不足" in answer
        or "見つからなかった" in answer
        or "見つかりません" in answer
        or "存在しません" in answer
        or "利用可能なデータが無い" in answer
    ):
        return True
    has_weak_phrase = any(pattern.lower() in normalized for pattern in _LOW_CONFIDENCE_DATA_AGENT_PATTERNS)
    has_specific_metric = bool(re.search(r"\d[\d,]*(?:\s*)(?:円|件|名|★|/5)", answer))
    return has_weak_phrase and not has_specific_metric


def _resolve_fabric_data_agent_runtime() -> str:
    """Fabric Data Agent REST を使うか、安定した SQL 経路を優先するかを返す。"""
    raw = str(get_settings().get("fabric_data_agent_runtime", "") or "").strip().lower()
    if raw in {"true", "1", "yes", "enabled", "rest", "data_agent"}:
        return "rest"
    if raw in {"auto"}:
        return "rest"
    return "sql"


def _build_data_agent_question(question: str) -> str:
    """Data Agent に対して、該当ゼロ時も代替集計を返すよう明示する。"""
    return "\n".join(
        [
            "旅行マーケティング施策のために Lakehouse データを分析してください。",
            "利用可能な主な列は travel_sales(Transaction_ID, Date, Travel_destination, Category, Schedule, Price, Price_per_person, Number_of_people, Age_group) と travel_review(Transaction_ID, Travel_destination, Rating, Emotions, Comments) です。",
            "Date は yyyy/MM/dd 形式です。月条件は /06/ /07/ /08/ のようなスラッシュ付き月、または日付として解釈できる方法で扱い、-06- のようなハイフン形式は使わないでください。",
            "Category は 国内/海外 の区分です。夏季/春季などの季節判定には絶対に使わず、季節は Date の月だけから判断してください。",
            "「ファミリー」「子連れ」「家族」は travel_sales.Number_of_people >= 3 または Age_group が 30代/40代の販売履歴、review Comments に 子連れ/子ども/家族 を含むレビューとして扱ってください。",
            "「学生」は Age_group が 20代、または若年グループ旅行として Number_of_people >= 2 を優先し、「若年層」は Age_group が 20代/30代、「シニア」は 50代以上、「春休み」は Date の月が 3/4 月のデータとして扱ってください。",
            "前年同期比較は複数年の同月データがある場合だけ行い、足りない場合は利用可能な期間のトレンドを示してください。",
            "売上上位は Travel_destination と Schedule で集計し、Transaction_ID 単位に分解せず、SUM(Price)、COUNT(Transaction_ID)、SUM(Number_of_people) を返してください。",
            "レビューは Travel_destination で絞り、COUNT、AVG(Rating)、Rating 分布、Comments の代表例を返してください。",
            "正確な条件一致がない場合でも、回答不能で終わらず、条件を少し広げた近いデータを使ってください。",
            "必ず売上額、予約件数、人数、レビュー件数、評価分布、代表的なレビューコメントを含めてください。",
            "実データがない項目は「データなし」と書き、X/XX/XXX や「例」の値は絶対に使わないでください。",
            "対象データが少ない場合は、その制約を一文で述べたうえで利用可能な上位データを提示してください。",
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
    """
    settings = get_settings()
    base_url = settings.get("fabric_data_agent_url", "")
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
        assistant = client.beta.assistants.create(model="not used")
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=_build_data_agent_question(question),
        )
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id,
        )

        # ポーリング（最大 90 秒）。Fabric Data Agent は NL2SQL 生成と検証で
        # 60 秒を超えることがあるため、デモ中の不要な SQL 退避を避ける。
        import time as _time

        terminal_states = {"completed", "failed", "cancelled", "requires_action"}
        start = _time.time()
        while run.status not in terminal_states:
            if _time.time() - start > _DATA_AGENT_POLL_TIMEOUT_SECONDS:
                logger.warning("Fabric Data Agent: ポーリングタイムアウト (status=%s)", run.status)
                break
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            await __import__("asyncio").sleep(2)

        if run.status != "completed":
            last_error = getattr(run, "last_error", None)
            logger.warning("Fabric Data Agent: run 失敗 (status=%s, last_error=%s)", run.status, last_error)
            # クリーンアップ
            try:
                client.beta.threads.delete(thread.id)
            except (ValueError, OSError):
                pass
            return None

        # 応答メッセージ取得
        messages = client.beta.threads.messages.list(thread_id=thread.id, order="asc")
        answer_parts: list[str] = []
        for msg in messages:
            if msg.role == "assistant":
                for content in msg.content:
                    if hasattr(content, "text"):
                        answer_parts.append(content.text.value)
        tool_outputs: list[str] = []
        try:
            steps = client.beta.threads.runs.steps.list(thread_id=thread.id, run_id=run.id, order="asc")
            tool_outputs = _extract_data_agent_tool_outputs(steps)
        except (AttributeError, ValueError, OSError) as exc:
            logger.warning("Fabric Data Agent: run steps 取得失敗: %s", exc)

        # クリーンアップ
        try:
            client.beta.threads.delete(thread.id)
        except (ValueError, OSError):
            pass

        answer = "\n".join(answer_parts).strip()
        if answer and tool_outputs and _is_low_confidence_data_agent_answer(answer):
            tool_answer = "\n\n".join(tool_outputs)
            if not _is_low_confidence_data_agent_answer(tool_answer):
                answer = (
                    "Fabric Data Agent の最終回答が十分な実数を含まなかったため、"
                    "Data Agent の実行結果を根拠として返します。\n"
                    f"{tool_answer}\n\n"
                    f"Data Agent 最終回答:\n{answer}"
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
    is_ws3iq_schema = {
        "travel_destination",
        "date",
        "price",
        "number_of_people",
        "age_group",
    }.issubset(table_columns)

    where_clauses: list[str] = []
    params: list = []

    if region:
        where_clauses.append("Travel_destination LIKE ?" if is_ws3iq_schema else "destination LIKE ?")
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
            date_expr = "TRY_CONVERT(date, [Date], 111)" if is_ws3iq_schema else "departure_date"
            where_clauses.append(f"MONTH({date_expr}) IN ({placeholders})")
            params.extend(months)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if is_ws3iq_schema:
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
    is_ws3iq_schema = {"travel_destination", "rating", "comments"}.issubset(table_columns)

    where_clauses: list[str] = []
    params: list = []

    if plan_name:
        where_clauses.append("Travel_destination LIKE ?" if is_ws3iq_schema else "plan_name LIKE ?")
        params.append(f"%{plan_name}%")

    if min_rating is not None:
        where_clauses.append("Rating >= ?" if is_ws3iq_schema else "rating >= ?")
        params.append(min_rating)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if is_ws3iq_schema:
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
        "同じ ws-3iq-demo Lakehouse の SQL endpoint から実データを集計しました。",
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

    Args:
        question: データに関する質問（例: 「沖縄プランの季節別売上推移は？」）
    """
    async with trace_tool_invocation("query_data_agent", agent_name="data-search-agent"):
        runtime = _resolve_fabric_data_agent_runtime()
        result = await _query_data_agent(question) if runtime == "rest" else None
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
                        "metadata": {"runtime": "fabric_data_agent"},
                    }
                ],
            )
            return json.dumps(
                {"source": "Fabric Data Agent", "answer": result},
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
                answer = (
                    "Fabric Data Agent の回答が十分な具体データを含まなかったため、"
                    "同じ ws-3iq-demo Lakehouse の SQL endpoint で補強しました。\n"
                    f"{fabric_sql_answer}"
                )
                source = "Fabric Data Agent + Fabric SQL"
                metadata = {"runtime": "fabric_sql_supplement", "data_agent_quality": "low_confidence"}
                title = "Fabric SQL 補強"
                relevance = 0.9
            elif runtime != "rest":
                answer = (
                    "Fabric Data Agent REST 経路は preview の thread 再利用で不安定なため、"
                    "デモ安定性を優先して同じ ws-3iq-demo Lakehouse の SQL endpoint で直接分析しました。\n"
                    f"{fabric_sql_answer}"
                )
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

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
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
