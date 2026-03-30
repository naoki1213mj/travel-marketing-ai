"""Agent2: マーケ施策作成エージェント。分析結果をもとに企画書を生成する。"""

import logging

from agent_framework.azure import AzureOpenAIResponsesClient
from azure.identity import DefaultAzureCredential

from src.config import get_settings

logger = logging.getLogger(__name__)


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


def create_marketing_plan_agent(model_settings: dict | None = None):
    """マーケ施策作成エージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
        deployment_name=settings["model_name"],
    )

    # Foundry 組み込み Web Search（Grounding with Bing Search）を使用
    # 別途 Bing リソースは不要 — Foundry プロジェクト経由で自動接続される
    agent_tools: list = [
        client.get_web_search_tool(
            user_location={"country": "JP", "region": "Tokyo"},
            search_context_size="medium",
        )
    ]

    agent_kwargs: dict = {
        "name": "marketing-plan-agent",
        "instructions": INSTRUCTIONS,
        "tools": agent_tools,
    }
    if model_settings:
        opts: dict = {}
        if "temperature" in model_settings:
            opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            opts["top_p"] = model_settings["top_p"]
        if opts:
            agent_kwargs["default_options"] = opts
    return client.as_agent(**agent_kwargs)
