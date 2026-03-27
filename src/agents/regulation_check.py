"""Agent3: レギュレーションチェックエージェント。企画書の法令・規制適合性を確認する。"""

import json

from agent_framework import AzureOpenAIResponsesClient, tool
from azure.identity import DefaultAzureCredential

from src.config import get_settings

# --- NG 表現リスト（Foundry IQ 未接続時のフォールバック） ---

NG_EXPRESSIONS = [
    {"expression": "最安値", "reason": "景品表示法 - 有利誤認のおそれ", "suggestion": "お得な価格帯"},
    {"expression": "業界No.1", "reason": "景品表示法 - 優良誤認のおそれ", "suggestion": "多くのお客様に選ばれている"},
    {"expression": "絶対", "reason": "景品表示法 - 断定的表現", "suggestion": "きっと（推量表現に変更）"},
    {"expression": "完全保証", "reason": "景品表示法 - 有利誤認のおそれ", "suggestion": "充実のサポート体制"},
    {"expression": "今だけ", "reason": "景品表示法 - 有利誤認（期間限定の根拠が必要）", "suggestion": "期間限定（具体的な期日を明記）"},
]

TRAVEL_LAW_CHECKLIST = [
    "書面交付義務: 取引条件を書面で明示しているか",
    "広告表示規制: 旅行業者の登録番号を表示しているか",
    "取引条件明示: 旅行代金・日程・宿泊先・交通手段を明記しているか",
    "取消料規定: キャンセル料の規定を明記しているか",
    "企画旅行: 主催旅行会社の責任範囲を明記しているか",
]


# --- ツール定義 ---

@tool
async def check_ng_expressions(text: str) -> str:
    """テキスト内の NG 表現（禁止表現）を検出する。

    Args:
        text: チェック対象のテキスト
    """
    found = []
    for ng in NG_EXPRESSIONS:
        if ng["expression"] in text:
            found.append(ng)
    return json.dumps(found, ensure_ascii=False) if found else "NG 表現は検出されませんでした。"


@tool
async def check_travel_law_compliance(document: str) -> str:
    """旅行業法の必須記載事項の適合性をチェックする。

    Args:
        document: チェック対象の企画書テキスト
    """
    # TODO: Foundry IQ Knowledge Base からの agentic retrieval に置き換え
    results = []
    for item in TRAVEL_LAW_CHECKLIST:
        # 簡易判定（実際は LLM ベースの判定になる）
        results.append({"check_item": item, "status": "要確認"})
    return json.dumps(results, ensure_ascii=False)


INSTRUCTIONS = """\
あなたは旅行業界の法規制チェックエージェントです。
Agent2（施策生成エージェント）が作成した企画書を受け取り、以下の観点でレギュレーションチェックを行ってください。

## チェック項目
1. **旅行業法チェック**: 書面交付義務・広告表示規制・取引条件明示の適合性
2. **景品表示法チェック**: 有利誤認・優良誤認・二重価格表示の違反がないか
3. **ブランドガイドラインチェック**: トーン＆マナー・ロゴ使用規定への準拠
4. **NG 表現検出**: 禁止表現（「最安値」「業界No.1」「絶対」等）の検出
5. **外部安全情報**: 目的地の外務省危険情報・気象警報（Web Search ツールがあれば確認）

## 出力フォーマット（Markdown）
1. チェック結果一覧（✅ 適合 / ⚠️ 要修正 / ❌ 違反）
2. 違反・要修正箇所の具体的な指摘
3. 修正提案（元の表現 → 修正案）
4. 修正を反映した企画書（Markdown）

必ず `check_ng_expressions` と `check_travel_law_compliance` ツールを使ってチェックしてください。
"""


def create_regulation_check_agent():
    """レギュレーションチェックエージェントを作成する"""
    settings = get_settings()
    client = AzureOpenAIResponsesClient(
        project_endpoint=settings["project_endpoint"],
        credential=DefaultAzureCredential(),
    )
    return client.as_agent(
        name="regulation-check-agent",
        instructions=INSTRUCTIONS,
        tools=[check_ng_expressions, check_travel_law_compliance],
        model=settings["model_name"],
    )
