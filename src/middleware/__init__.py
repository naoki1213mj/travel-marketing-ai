"""Content Safety ミドルウェアと Prompt Shield / Text Analysis のチェック関数。"""

import logging
import os
from dataclasses import dataclass

from src.config import is_production_environment

logger = logging.getLogger(__name__)


@dataclass
class ShieldResult:
    """Prompt Shield チェック結果"""

    is_safe: bool
    details: dict | None = None


@dataclass
class SafetyScores:
    """Content Safety Text Analysis のスコア"""

    hate: int = 0
    self_harm: int = 0
    sexual: int = 0
    violence: int = 0
    check_failed: bool = False


def _content_safety_required() -> bool:
    """現在の環境で Content Safety を必須とするかを返す。"""
    return is_production_environment()


def _get_content_safety_client():
    """Content Safety クライアントを返す。利用不可なら None。"""
    endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
    if not endpoint:
        return None, endpoint
    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.identity import DefaultAzureCredential

        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        return client, endpoint
    except ImportError:
        logger.warning("azure-ai-contentsafety がインストールされていません")
        return None, endpoint


async def check_prompt_shield(user_input: str) -> ShieldResult:
    """Prompt Shield でユーザー入力をチェックする（層1）"""
    client, endpoint = _get_content_safety_client()
    if not endpoint:
        if _content_safety_required():
            logger.error("CONTENT_SAFETY_ENDPOINT が未設定のため Prompt Shield をブロック")
            return ShieldResult(is_safe=False, details={"reason": "missing_endpoint"})
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Prompt Shield をスキップ")
        return ShieldResult(is_safe=True, details={"reason": "skipped_local"})

    if client is None:
        if _content_safety_required():
            return ShieldResult(is_safe=False, details={"reason": "client_unavailable"})
        return ShieldResult(is_safe=True, details={"reason": "client_unavailable"})

    try:
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory

        options = AnalyzeTextOptions(
            text=user_input,
            categories=[TextCategory.HATE, TextCategory.SELF_HARM, TextCategory.SEXUAL, TextCategory.VIOLENCE],
        )
        response = client.analyze_text(options)
        is_safe = all(c.severity == 0 for c in response.categories_analysis)
        return ShieldResult(is_safe=is_safe, details={"categories": str(response.categories_analysis)})
    except Exception:
        logger.exception("Prompt Shield チェックでエラーが発生")
        if _content_safety_required():
            logger.error("本番環境: Prompt Shield 障害のため入力をブロック (fail-close)")
            return ShieldResult(is_safe=False, details={"reason": "check_failed"})
        return ShieldResult(is_safe=False, details={"reason": "check_failed"})


async def check_tool_response(tool_output: str) -> ShieldResult:
    """ツール応答に対する Prompt Shield チェック（層3）

    Web Search や MCP ツールから返された外部データに対して、
    プロンプトインジェクション攻撃が含まれていないかを検証する。
    """
    client, endpoint = _get_content_safety_client()
    if not endpoint:
        if _content_safety_required():
            return ShieldResult(is_safe=False, details={"reason": "missing_endpoint"})
        return ShieldResult(is_safe=True, details={"reason": "skipped_local"})

    if client is None:
        if _content_safety_required():
            return ShieldResult(is_safe=False, details={"reason": "client_unavailable"})
        return ShieldResult(is_safe=True, details={"reason": "client_unavailable"})

    try:
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory

        # ツール応答は短く切り詰めてチェック（コスト最適化）
        truncated = tool_output[:4000] if len(tool_output) > 4000 else tool_output
        options = AnalyzeTextOptions(
            text=truncated,
            categories=[TextCategory.HATE, TextCategory.SELF_HARM, TextCategory.SEXUAL, TextCategory.VIOLENCE],
        )
        response = client.analyze_text(options)
        is_safe = all(c.severity == 0 for c in response.categories_analysis)
        if not is_safe:
            logger.warning("ツール応答に安全でないコンテンツを検出しました")
        return ShieldResult(is_safe=is_safe, details={"categories": str(response.categories_analysis)})
    except Exception:
        logger.exception("ツール応答 Prompt Shield でエラーが発生")
        if _content_safety_required():
            return ShieldResult(is_safe=False, details={"reason": "check_failed"})
        return ShieldResult(is_safe=True, details={"reason": "check_failed_dev"})


async def analyze_content(text: str) -> SafetyScores:
    """Text Analysis で出力コンテンツをチェックする（層4）"""
    client, endpoint = _get_content_safety_client()
    if not endpoint:
        if _content_safety_required():
            logger.error("CONTENT_SAFETY_ENDPOINT が未設定のため Text Analysis をブロック")
            return SafetyScores(check_failed=True)
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Text Analysis をスキップ")
        return SafetyScores()

    if client is None:
        if _content_safety_required():
            return SafetyScores(check_failed=True)
        return SafetyScores()

    try:
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory

        options = AnalyzeTextOptions(
            text=text,
            categories=[TextCategory.HATE, TextCategory.SELF_HARM, TextCategory.SEXUAL, TextCategory.VIOLENCE],
        )
        response = client.analyze_text(options)
        scores = SafetyScores()
        for cat in response.categories_analysis:
            if cat.category == "Hate":
                scores.hate = cat.severity
            elif cat.category == "SelfHarm":
                scores.self_harm = cat.severity
            elif cat.category == "Sexual":
                scores.sexual = cat.severity
            elif cat.category == "Violence":
                scores.violence = cat.severity
        return scores
    except Exception:
        logger.exception("Text Analysis でエラーが発生")
        if _content_safety_required():
            logger.error("本番環境: Text Analysis 障害のためチェック失敗扱い (fail-close)")
        return SafetyScores(check_failed=True)
