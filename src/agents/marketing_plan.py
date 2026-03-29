"""Agent2: マーケ施策作成エージェント。分析結果をもとに企画書を生成する。"""

import logging

from agent_framework import tool
from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)


@tool
async def search_market_trends(query: str) -> str:
    """最新の旅行市場トレンドや競合情報を Web 検索する。

    Args:
        query: 検索クエリ（例: 「2026年春 沖縄旅行 トレンド」）
    """
    # Foundry Agent Service の Web Search ツールが利用可能な場合はそちらが優先される
    # ローカル開発時はフォールバックとして静的データを返す
    logger.info("Web 検索フォールバック: %s", query)
    return (
        "【市場トレンド情報】\n"
        "- 2026年春の沖縄旅行は前年比15%増の見込み\n"
        "- ファミリー層・アクティビティ体験型が人気上昇中\n"
        "- 美ら海水族館リニューアル効果で北部エリアの需要増加\n"
        "- SNS映えスポット巡りツアーが新しいトレンド\n"
        "- サステナブルツーリズム（エコツアー）への関心が高まる"
    )

INSTRUCTIONS = """\
あなたは旅行マーケティングの施策立案エージェントです。
Agent1（データ検索エージェント）の分析結果を受け取り、以下の構成で Markdown 形式の企画書を生成してください。

## 企画書の構成
1. **タイトル**: プラン名（キャッチーな名前）
2. **キャッチコピー案**: 3 パターン以上
3. **ターゲット**: 具体的なペルソナ（年代・家族構成・旅行動機）
4. **プラン概要**: 日数・ルート・価格帯・含まれるもの
5. **差別化ポイント**: 競合との違い、データに基づく強み
6. **改善ポイント**: 顧客の不満点への対策
7. **販促チャネル**: SNS・Web・メルマガ等の展開案
8. **KPI**: 目標予約数・売上・前年比

## ルール
- データ分析結果を必ず根拠として引用する
- 顧客の不満点を改善ポイントとして反映する
- 景品表示法に抵触しそうな表現（「最安値」「業界No.1」等）は避ける
- Web Search ツールがあれば、最新の旅行トレンドや競合情報を取得して反映する

出力は Markdown 形式で、見出し・箇条書き・太字を適切に使ってください。
"""


def create_marketing_plan_agent():
    """マーケ施策作成エージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=settings["model_name"],
    )
    return client.as_agent(
        name="marketing-plan-agent",
        instructions=INSTRUCTIONS,
        tools=[search_market_trends],
    )
