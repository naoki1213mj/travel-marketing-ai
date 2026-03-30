"""Foundry Evaluations 実行スクリプト。

azure-ai-evaluation SDK でパイプライン出力の品質を評価する。
social-ai-studio の評価パターンを参考に実装。

使い方:
    uv run python scripts/run_evaluations.py

必要な環境変数:
    AZURE_AI_PROJECT_ENDPOINT: Foundry プロジェクトの endpoint
    EVAL_MODEL_DEPLOYMENT: 評価に使うモデル deployment 名（デフォルト: gpt-5-4-mini）
"""

import json
import os
import sys
from urllib.parse import urlparse


def main():
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    eval_model = os.environ.get("EVAL_MODEL_DEPLOYMENT", "gpt-5-4-mini")
    if not endpoint:
        print("❌ AZURE_AI_PROJECT_ENDPOINT 環境変数を設定してください")
        sys.exit(1)

    print(f"🔗 プロジェクト: {endpoint}")
    print(f"🤖 評価モデル: {eval_model}")

    from azure.ai.evaluation import CoherenceEvaluator, FluencyEvaluator, RelevanceEvaluator

    # AI Services のリソースレベル endpoint を導出
    # social-ai-studio パターン: project endpoint からスキーム + ホスト部分のみ抽出
    parsed = urlparse(endpoint)
    azure_endpoint = f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else endpoint

    # AAD トークンを api_key として渡す（social-ai-studio パターン）
    try:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()
        token = credential.get_token("https://cognitiveservices.azure.com/.default")
        api_key = token.token
    except Exception as e:
        print(f"❌ 認証に失敗: {e}")
        sys.exit(1)

    model_config = {
        "azure_endpoint": azure_endpoint,
        "azure_deployment": eval_model,
        "api_version": "2024-10-21",
        "api_key": api_key,
    }

    eval_data = [
        {
            "query": "沖縄3泊4日のファミリー向け春季プランを作って",
            "response": (
                "# 春の沖縄ファミリープラン\n\n"
                "## キャッチコピー\n「家族で発見！春色おきなわ」\n\n"
                "## ターゲット\n小学生連れファミリー（30〜40代）\n\n"
                "## プラン概要\n3泊4日 89,800円〜（税込）\n\n"
                "## KPI\n月間予約数100件"
            ),
        },
        {
            "query": "北海道の冬季スキーツアーの企画書を作成してください",
            "response": (
                "# 北海道パウダースノー スキーツアー\n\n"
                "## キャッチコピー\n「極上の粉雪体験」\n\n"
                "## ターゲット\nスキー愛好家（20〜50代）\n\n"
                "## プラン概要\n2泊3日 69,800円〜（税込）\n\n"
                "## KPI\n月間予約数50件"
            ),
        },
    ]

    # gpt-5 系モデルは max_tokens 非対応。is_reasoning_model=True で max_completion_tokens を使う
    evaluators = {
        "relevance": RelevanceEvaluator(model_config=model_config, is_reasoning_model=True),
        "coherence": CoherenceEvaluator(model_config=model_config, is_reasoning_model=True),
        "fluency": FluencyEvaluator(model_config=model_config, is_reasoning_model=True),
    }

    print("\n📊 品質評価を実行中...")
    results = []
    for i, data in enumerate(eval_data):
        print(f"\n--- 評価 {i + 1}/{len(eval_data)} ---")
        print(f"  クエリ: {data['query'][:50]}...")
        scores: dict[str, object] = {}
        for name, evaluator in evaluators.items():
            try:
                result = evaluator(query=data["query"], response=data["response"])
                score = result.get(name, result.get(f"gpt_{name}", "N/A"))
                reason = result.get(f"{name}_reason", "")
                scores[name] = float(score) if score is not None else -1
                if reason:
                    scores[f"{name}_reason"] = reason
                print(f"  {name}: {score}")
            except Exception as e:
                print(f"  {name}: エラー ({e})")
                scores[name] = -1
        results.append({"query": data["query"][:50], "scores": scores})

    print(f"\n📋 結果サマリ:\n{json.dumps(results, indent=2, ensure_ascii=False)}")
    print("\n✅ 評価完了！")


if __name__ == "__main__":
    main()
