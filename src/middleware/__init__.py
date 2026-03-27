"""Content Safety ミドルウェアと Prompt Shield / Text Analysis のチェック関数。"""

import logging
import os
from dataclasses import dataclass

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


async def check_prompt_shield(user_input: str) -> ShieldResult:
    """Prompt Shield でユーザー入力をチェックする（層1）"""
    endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
    if not endpoint:
        # Content Safety 未設定の場合はスキップ（開発環境用）
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Prompt Shield をスキップ")
        return ShieldResult(is_safe=True)

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.identity import DefaultAzureCredential

        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        response = client.analyze_text(
            text=user_input,
            categories=["Hate", "SelfHarm", "Sexual", "Violence"],
            output_type="FourSeverityLevels",
        )
        shield_response = client.detect_jailbreak(text=user_input)
        is_safe = (
            all(c.severity == 0 for c in response.categories_analysis)
            and not shield_response.jailbreak_detected
        )
        return ShieldResult(is_safe=is_safe, details={"categories": str(response.categories_analysis)})
    except ImportError:
        logger.warning("azure-ai-contentsafety がインストールされていません")
        return ShieldResult(is_safe=True)
    except Exception:
        logger.exception("Prompt Shield チェックでエラーが発生")
        return ShieldResult(is_safe=True)


async def analyze_content(text: str) -> SafetyScores:
    """Text Analysis で出力コンテンツをチェックする（層4）"""
    endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
    if not endpoint:
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Text Analysis をスキップ")
        return SafetyScores()

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.identity import DefaultAzureCredential

        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        response = client.analyze_text(
            text=text,
            categories=["Hate", "SelfHarm", "Sexual", "Violence"],
            output_type="FourSeverityLevels",
        )
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
        return SafetyScores()
