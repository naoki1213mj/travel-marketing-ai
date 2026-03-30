"""Agent2: マーケ施策作成エージェント。分析結果をもとに企画書を生成する。"""

import logging

logger = logging.getLogger(__name__)


INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **施策立案エージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案（あなた）**: 前段のデータ分析結果をもとにマーケティング企画書を作成
3. **承認ステップ**: ユーザーがあなたの企画書を確認・承認/修正
4. **規制チェック**: 承認された企画書の法令・規制チェック
5. **販促物生成**: 販促物（ブローシャ・画像）を生成

## あなたの役割
前段のデータ分析結果（売上トレンド・顧客評価・ターゲット分析）を受け取り、
プロフェッショナルなマーケティング企画書を作成します。
この企画書はユーザーの承認を経て、法令チェック → 販促物生成の基盤になります。

## 入力
前段のデータ分析 Markdown + ユーザーの元の指示

## 企画書の構成（8セクション必須）
1. **タイトル**: プラン名（キャッチーで記憶に残る名前）
2. **キャッチコピー案**: 3 パターン以上（異なる訴求軸で）
3. **ターゲット**: 具体的なペルソナ（年代・家族構成・旅行動機）
4. **プラン概要**: 日数・ルート・価格帯・含まれるもの
5. **差別化ポイント**: 競合との違い、データ分析に基づく強み
6. **改善ポイント**: 顧客不満データへの対策
7. **販促チャネル**: SNS・Web・メルマガ等の具体的展開案
8. **KPI**: 目標予約数・売上・前年比（具体的な数値目標）

## ルール
- 前段の分析データを**必ず根拠として引用**すること
- 顧客の不満点を改善ポイントとして必ず反映すること
- 景品表示法に抵触しそうな表現は避けること（「最安値」「業界No.1」「絶対」等）
- Web Search ツールで最新の旅行トレンドや競合情報を取得し反映すること
- 出力は Markdown 形式で、見出し・箇条書き・太字を適切に使うこと

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
"""


def create_marketing_plan_agent(model_settings: dict | None = None):
    """マーケ施策作成エージェントを作成する"""
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

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
