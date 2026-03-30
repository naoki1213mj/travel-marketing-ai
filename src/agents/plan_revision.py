"""Agent3b: 企画書修正エージェント。規制チェック結果を反映した修正版企画書を生成する。"""

import logging

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **企画書修正エージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案**: マーケティング企画書の作成（完了済み）
3. **承認ステップ**: ユーザーが企画書を承認（完了済み）
4. **規制チェック**: 法令・規制適合性の検証（完了済み）
5. **企画書修正（あなた）**: 規制チェック結果を反映した修正版企画書を生成
6. **販促物生成**: HTML ブローシャ・画像・動画の生成

## あなたの役割
規制チェックの結果（違反指摘・修正提案）と元の企画書を受け取り、
すべての指摘事項を反映した**完全な修正版企画書**を出力します。
この企画書が販促物生成の基盤になるため、品質が極めて重要です。

## 入力
- 元の企画書（Markdown）
- 規制チェック結果（✅/⚠️/❌ テーブル + 修正提案）

## 出力ルール
- 企画書の全セクション（タイトル〜KPI）を**省略せずに完全に出力**すること
- 規制チェックで指摘された箇所は修正提案に従って修正すること
- 修正していない箇所も含め、企画書全体を出力すること
- チェック結果テーブルや指摘内容は出力に含めないこと（修正済み企画書のみ）
- 「以下省略」「同上」等の省略は禁止

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
"""


def create_plan_revision_agent(model_settings: dict | None = None):
    """企画書修正エージェントを作成する。"""
    from src.agent_client import get_responses_client

    deployment = None
    if model_settings and model_settings.get("model"):
        deployment = model_settings["model"]
    client = get_responses_client(deployment)

    agent_kwargs: dict = {
        "name": "plan-revision-agent",
        "instructions": INSTRUCTIONS,
        "tools": [],
    }
    # 修正版企画書は完全出力が必要
    default_opts: dict = {"max_output_tokens": 8192}
    if model_settings:
        if "temperature" in model_settings:
            default_opts["temperature"] = model_settings["temperature"]
        if "max_tokens" in model_settings:
            default_opts["max_output_tokens"] = model_settings["max_tokens"]
        if "top_p" in model_settings:
            default_opts["top_p"] = model_settings["top_p"]
    agent_kwargs["default_options"] = default_opts
    return client.as_agent(**agent_kwargs)
