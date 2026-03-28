"""Agent1: データ検索エージェント。Fabric Lakehouse から販売・顧客データを検索・分析する。"""

import csv
import json
from pathlib import Path

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

# --- デモデータ読み込み（Fabric Lakehouse 未接続時は CSV から読み込む） ---

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def _load_csv(filename: str) -> list[dict]:
    """CSV ファイルからデータを読み込む"""
    filepath = DATA_DIR / filename
    if not filepath.exists():
        return []
    with open(filepath, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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
    results = _get_reviews()
    if plan_name:
        results = [r for r in results if plan_name in r["plan_name"]]
    if min_rating is not None:
        results = [r for r in results if r["rating"] >= min_rating]
    return json.dumps(results, ensure_ascii=False)


# --- エージェント作成 ---

INSTRUCTIONS = """\
あなたは旅行データの分析エージェントです。
ユーザーの指示からターゲット・季節・地域・予算等を抽出し、
販売履歴と顧客レビューを検索・分析して、以下のサマリを生成してください。

## 出力フォーマット（Markdown）
1. **ターゲット分析**: 抽出したターゲット情報
2. **売上トレンド**: 前年比・セグメント比率
3. **顧客評価**: 人気ポイント・不満点
4. **推奨**: データに基づく施策の方向性

売上データと顧客レビューのツールを必ず使って分析してください。
"""


def create_data_search_agent():
    """データ検索エージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=settings["model_name"],
    )
    return client.as_agent(
        name="data-search-agent",
        instructions=INSTRUCTIONS,
        tools=[search_sales_history, search_customer_reviews],
    )
