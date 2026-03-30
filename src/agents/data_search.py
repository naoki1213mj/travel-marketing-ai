"""Agent1: データ検索エージェント。Fabric Lakehouse から販売・顧客データを検索・分析する。"""

import csv
import json
import logging
import os
import struct
from pathlib import Path

from agent_framework import tool
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel

from src.config import get_settings

try:
    import pyodbc

    _HAS_PYODBC = True
except ImportError:
    _HAS_PYODBC = False

logger = logging.getLogger(__name__)


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
        from azure.identity import DefaultAzureCredential as _Cred

        credential = _Cred()
        token = credential.get_token("https://analysis.windows.net/powerbi/api/.default")
    except (ValueError, OSError) as exc:
        logger.warning("Fabric Data Agent: トークン取得失敗: %s", exc)
        return None

    try:
        from openai import OpenAI

        # Fabric Data Agent は OpenAI Assistants API 互換エンドポイントを公開する
        client = OpenAI(
            base_url=base_url,
            api_key="",
            default_headers={"Authorization": f"Bearer {token.token}"},
        )

        # スレッド作成 → メッセージ送信 → 実行 → 結果取得
        assistant = client.beta.assistants.create(model="not used")
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=question,
        )
        run = client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=assistant.id,
        )

        # ポーリング（最大 60 秒）
        import time as _time

        terminal_states = {"completed", "failed", "cancelled", "requires_action"}
        start = _time.time()
        while run.status not in terminal_states:
            if _time.time() - start > 60:
                logger.warning("Fabric Data Agent: ポーリングタイムアウト (status=%s)", run.status)
                break
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            await __import__("asyncio").sleep(2)

        if run.status != "completed":
            logger.warning("Fabric Data Agent: run 失敗 (status=%s)", run.status)
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

        # クリーンアップ
        try:
            client.beta.threads.delete(thread.id)
        except (ValueError, OSError):
            pass

        answer = "\n".join(answer_parts).strip()
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

    try:
        credential = DefaultAzureCredential()
        token = credential.get_token(_SQL_TOKEN_SCOPE)
        token_bytes = token.token.encode("utf-16-le")
        token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

        # SQL_COPT_SS_ACCESS_TOKEN = 1256
        conn = pyodbc.connect(
            f"Driver={{ODBC Driver 18 for SQL Server}};"
            f"Server={endpoint};"
            f"Database=Travel_Lakehouse;"
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

    except (pyodbc.Error, ValueError, OSError) as exc:
        logger.warning("Fabric SQL 接続エラー（CSV にフォールバック）: %s", exc)
        return []


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
    # departure_date から季節を SQL で導出して集約
    where_clauses: list[str] = []
    params: list = []

    if region:
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
            where_clauses.append(f"MONTH(departure_date) IN ({placeholders})")
            params.extend(months)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

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
        FROM sales_results
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
    where_clauses: list[str] = []
    params: list = []

    if plan_name:
        where_clauses.append("plan_name LIKE ?")
        params.append(f"%{plan_name}%")

    if min_rating is not None:
        where_clauses.append("rating >= ?")
        params.append(min_rating)

    where_sql = f" WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    query = f"""
        SELECT plan_name, rating, comment
        FROM customer_reviews
        {where_sql}
        ORDER BY review_date DESC
    """

    return _query_fabric(query, params if params else None)


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
    result = await _query_data_agent(question)
    if result:
        return json.dumps(
            {"source": "Fabric Data Agent", "answer": result},
            ensure_ascii=False,
        )
    return json.dumps(
        {"source": "fallback", "message": "Fabric Data Agent は現在利用できません。search_sales_history / search_customer_reviews をお使いください。"},
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
    # Fabric SQL を優先し、取得できなければ CSV にフォールバック
    results = _get_sales_data_from_fabric(season=season, region=region)
    if results:
        logger.info("Fabric SQL から販売データ %d 件取得", len(results))
        return json.dumps(results, ensure_ascii=False, default=str)

    logger.info("CSV フォールバックで販売データを取得")
    results = _get_sales_data()
    if season:
        results = [r for r in results if r["season"] == season]
    if region:
        results = [r for r in results if region in r["destination"]]
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
    # Fabric SQL を優先し、取得できなければ CSV にフォールバック
    results = _get_reviews_from_fabric(plan_name=plan_name, min_rating=min_rating)
    if results:
        logger.info("Fabric SQL からレビュー %d 件取得", len(results))
        return json.dumps(results, ensure_ascii=False, default=str)

    logger.info("CSV フォールバックでレビューデータを取得")
    results = _get_reviews()
    if plan_name:
        results = [r for r in results if plan_name in r["plan_name"]]
    if min_rating is not None:
        results = [r for r in results if r["rating"] >= min_rating]
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
