"""Agent1: データ検索エージェント。Fabric Lakehouse から販売・顧客データを検索・分析する。"""

import json

from agent_framework import AzureOpenAIResponsesClient, tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings

# --- モックデータ（Fabric Lakehouse 未接続時のフォールバック） ---

MOCK_SALES_DATA = [
    {"plan_name": "沖縄3泊4日ファミリープラン", "destination": "沖縄", "season": "spring",
     "revenue": 358400, "pax": 4, "customer_segment": "ファミリー", "booking_count": 45},
    {"plan_name": "沖縄リゾートステイ", "destination": "沖縄", "season": "spring",
     "revenue": 198000, "pax": 2, "customer_segment": "カップル", "booking_count": 32},
    {"plan_name": "北海道ラベンダー畑ツアー", "destination": "北海道", "season": "summer",
     "revenue": 275000, "pax": 3, "customer_segment": "ファミリー", "booking_count": 28},
    {"plan_name": "京都 紅葉めぐり", "destination": "京都", "season": "autumn",
     "revenue": 156000, "pax": 2, "customer_segment": "シニア", "booking_count": 55},
    {"plan_name": "箱根温泉週末プラン", "destination": "箱根", "season": "winter",
     "revenue": 89000, "pax": 2, "customer_segment": "カップル", "booking_count": 62},
]

MOCK_REVIEWS = [
    {"plan_name": "沖縄3泊4日ファミリープラン", "rating": 5, "comment": "子どもが大喜びでした。美ら海水族館が最高！"},
    {"plan_name": "沖縄3泊4日ファミリープラン", "rating": 4, "comment": "ホテルは清潔で良かったが、移動が多かった"},
    {"plan_name": "沖縄3泊4日ファミリープラン", "rating": 3, "comment": "価格に対して食事の質がイマイチ"},
    {"plan_name": "沖縄リゾートステイ", "rating": 5, "comment": "プールもビーチも最高のリゾート体験"},
    {"plan_name": "北海道ラベンダー畑ツアー", "rating": 4, "comment": "景色が素晴らしかった。食事も美味しい"},
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
    # TODO: Fabric SQL EP 経由のクエリに置き換え
    results = MOCK_SALES_DATA
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
    # TODO: Fabric SQL EP 経由のクエリに置き換え
    results = MOCK_REVIEWS
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
    )
    return client.as_agent(
        name="data-search-agent",
        instructions=INSTRUCTIONS,
        tools=[search_sales_history, search_customer_reviews],
        model=settings["model_name"],
    )
