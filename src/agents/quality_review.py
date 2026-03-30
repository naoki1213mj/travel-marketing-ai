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
あなたは旅行マーケティングの品質レビュー専門家です。
以下の観点で生成された成果物をレビューしてください。

## チェック項目
1. 企画書の構成品質（ターゲット定義・訴求ポイント・KPI の有無）
2. ブローシャ HTML のアクセシビリティ
3. テキストのトーン一貫性（ブランドガイドライン準拠）
4. 旅行業法の表記ルール準拠

ツールを使って自動チェックし、チェック結果を Markdown のチェックリスト形式で出力してください。
問題がなければ「品質チェック合格」と明記してください。
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

        review_agent = GitHubCopilotAgent(
            name="quality-review-agent",
            instructions=INSTRUCTIONS,
            tools=_REVIEW_TOOLS,
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
