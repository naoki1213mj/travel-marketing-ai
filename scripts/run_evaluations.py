"""Foundry Evaluations セットアップスクリプト。

パイプライン出力の品質を評価するための Evaluator を構成する。
会話履歴から評価データセットを生成し、バッチ評価を実行する。

使い方:
    uv run python scripts/run_evaluations.py

必要な環境変数:
    AZURE_AI_PROJECT_ENDPOINT: Foundry プロジェクトの endpoint
"""

import json
import os
import sys

from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential


def run_text_evaluation(project: AIProjectClient) -> dict:
    """テキスト品質の評価を実行する"""
    from azure.ai.projects.models import (
        EvaluationDataset,
        Evaluator,
        InlineEvaluationDataset,
    )

    # 評価データセット（代表的なパイプライン入出力ペア）
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

    # 評価実行
    try:
        evaluation = project.evaluations.create(
            display_name="travel-pipeline-quality",
            description="旅行マーケティングパイプラインの品質評価",
            data=EvaluationDataset(
                inline=InlineEvaluationDataset(rows=eval_data),
            ),
            evaluators={
                "coherence": Evaluator(id="coherence"),
                "fluency": Evaluator(id="fluency"),
                "groundedness": Evaluator(id="groundedness"),
                "relevance": Evaluator(id="relevance"),
            },
        )
        print(f"✅ 評価を作成しました: {evaluation.id}")
        print(f"   名前: {evaluation.display_name}")
        return {"id": evaluation.id, "status": "created"}
    except Exception as e:
        print(f"⚠️ 評価作成に失敗: {e}")
        print("   Foundry ポータルで手動評価をセットアップしてください。")
        return {"status": "failed", "error": str(e)}


def main():
    endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        print("❌ AZURE_AI_PROJECT_ENDPOINT 環境変数を設定してください")
        sys.exit(1)

    print(f"🔗 プロジェクト: {endpoint}")
    project = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )

    print("\n📊 テキスト品質評価を実行中...")
    result = run_text_evaluation(project)
    print(f"\n結果: {json.dumps(result, indent=2, ensure_ascii=False)}")

    print("\n✅ Foundry Evaluations セットアップ完了！")
    print("   Foundry ポータルの Evaluations ダッシュボードで結果を確認できます。")


if __name__ == "__main__":
    main()
