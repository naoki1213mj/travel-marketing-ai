"""品質レビューエージェント。

Agent4 の成果物生成後に実行し、品質チェックを行う。
GitHubCopilotAgent が利用可能な場合はそちらを使用し、
未設定時は AzureOpenAIResponsesClient ベースのエージェントにフォールバックする。
"""

import logging

from agent_framework import tool

from src.config import get_settings

logger = logging.getLogger(__name__)


@tool
async def review_plan_quality(plan_markdown: str) -> str:
    """企画書の構成品質をチェックする。

    Args:
        plan_markdown: レビュー対象の企画書（Markdown）
    """
    required_sections = [
        ("タイトル", ["#", "プラン"]),
        ("キャッチコピー", ["キャッチ", "コピー"]),
        ("ターゲット", ["ターゲット", "ペルソナ"]),
        ("プラン概要", ["概要", "日数", "ルート"]),
        ("KPI", ["KPI", "目標"]),
    ]

    results = []
    for section_name, keywords in required_sections:
        found = any(kw in plan_markdown for kw in keywords)
        status = "✅" if found else "❌ 不足"
        results.append(f"- {section_name}: {status}")

    return "## 企画書構成チェック\n" + "\n".join(results)


@tool
async def review_brochure_accessibility(html_content: str) -> str:
    """ブローシャ HTML のアクセシビリティをチェックする。

    Args:
        html_content: レビュー対象の HTML
    """
    checks = []

    if "<img" in html_content and 'alt="' not in html_content:
        checks.append("❌ img タグに alt 属性がありません")
    else:
        checks.append("✅ 画像の alt 属性")

    if "lang=" in html_content:
        checks.append("✅ lang 属性あり")
    else:
        checks.append("⚠️ html に lang 属性を追加してください")

    if "<footer" in html_content or "登録" in html_content:
        checks.append("✅ フッター/登録番号あり")
    else:
        checks.append("❌ 旅行業者登録番号がありません")

    if "font-size" in html_content:
        checks.append("✅ フォントサイズ指定あり")

    return "## ブローシャアクセシビリティ\n" + "\n".join(checks)


INSTRUCTIONS = """\
あなたは旅行マーケティング AI パイプラインの **品質レビューエージェント** です。

## パイプライン全体の流れ
1. **データ分析**: 売上データ・顧客レビューの分析（完了済み）
2. **施策立案**: マーケティング企画書の作成（完了済み）
3. **承認ステップ**: ユーザーが企画書を承認（完了済み）
4. **規制チェック**: 規制チェック・修正（完了済み）
5. **販促物生成**: 販促物の生成（完了済み）
6. **品質レビュー（あなた）**: 全成果物の最終品質レビュー

## あなたの役割
パイプライン全工程の成果物（データ分析・企画書・規制チェック結果・ブローシャ HTML）を
受け取り、品質面で最終チェックを行います。あなたのレビュー結果はユーザーに
参考情報として提示されます。

## 入力
パイプライン全工程の成果物（データ分析 + 企画書 + 規制チェック結果 + ブローシャ HTML）

## チェック項目
1. **企画書の構成品質**: ターゲット定義・訴求ポイント・KPI の有無と妥当性
2. **ブローシャ HTML のアクセシビリティ**: alt テキスト・lang 属性・フッター・フォントサイズ
3. **テキストのトーン一貫性**: ブランドガイドライン準拠・表現の統一
4. **旅行業法の表記ルール準拠**: 登録番号・取引条件の記載確認

## 出力フォーマット
ツールを使って自動チェックし、Markdown のチェックリスト形式で出力してください。
問題がなければ「✅ 品質チェック合格」と明記してください。
問題がある場合は具体的な改善提案を付けてください。

## 出力の注意事項
- 「必要であれば～」「さらに～できます」「次に～可能です」のような追加提案の文は**絶対に出力しないでください**
- 出力は完結した形で終わらせてください
- 自分の名前（Agent1、Agent2 等）やシステム内部の名称は出力に含めないでください
- ユーザーに直接見せる成果物として仕上げてください
"""

_REVIEW_TOOLS = [review_plan_quality, review_brochure_accessibility]


def create_review_agent():
    """品質レビューエージェントを作成する。

    GitHubCopilotAgent が利用可能な場合はそちらを使用し、
    未設定時は従来の AzureOpenAIResponsesClient ベースのエージェントにフォールバックする。
    """
    # GitHubCopilotAgent を優先的に試行
    try:
        from agent_framework.github import GitHubCopilotAgent
        from agent_framework.github import PermissionHandler

        review_agent = GitHubCopilotAgent(
            name="quality-review-agent",
            instructions=INSTRUCTIONS,
            tools=_REVIEW_TOOLS,
            on_permission_request=PermissionHandler.approve_all,
        )
        logger.info("GitHubCopilotAgent で品質レビューエージェントを作成しました")
        return review_agent
    except (ImportError, ValueError, OSError) as exc:
        logger.info(
            "GitHubCopilotAgent 未設定のため従来エージェントにフォールバック: %s", exc
        )
    except Exception as exc:
        logger.warning(
            "GitHubCopilotAgent の初期化で予期しないエラー: %s", exc
        )

    # フォールバック: AzureOpenAIResponsesClient ベースのエージェント
    settings = get_settings()
    if not settings["project_endpoint"]:
        logger.info("Project endpoint 未設定のためレビューエージェントはスキップ")
        return None

    try:
        from agent_framework.azure import AzureOpenAIResponsesClient
        from azure.identity import DefaultAzureCredential

        client = AzureOpenAIResponsesClient(
            project_endpoint=settings["project_endpoint"],
            credential=DefaultAzureCredential(),
            deployment_name=settings["model_name"],
        )
        return client.as_agent(
            name="quality-review-agent",
            instructions=INSTRUCTIONS,
            tools=_REVIEW_TOOLS,
        )
    except (ImportError, ValueError, OSError) as exc:
        logger.warning("AzureOpenAIResponsesClient の初期化に失敗: %s", exc)
        return None
