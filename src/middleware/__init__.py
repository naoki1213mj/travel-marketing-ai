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


async def check_prompt_shield(user_input: str) -> ShieldResult:
    """Prompt Shield でユーザー入力をチェックする（層1）"""
    endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
    if not endpoint:
        if _content_safety_required():
            logger.error("CONTENT_SAFETY_ENDPOINT が未設定のため Prompt Shield をブロック")
            return ShieldResult(is_safe=False, details={"reason": "missing_endpoint"})
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Prompt Shield をスキップ")
        return ShieldResult(is_safe=True, details={"reason": "skipped_local"})

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
        from azure.identity import DefaultAzureCredential

        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
        options = AnalyzeTextOptions(
            text=user_input,
            categories=[TextCategory.HATE, TextCategory.SELF_HARM, TextCategory.SEXUAL, TextCategory.VIOLENCE],
        )
        response = client.analyze_text(options)
        is_safe = all(c.severity == 0 for c in response.categories_analysis)
        return ShieldResult(is_safe=is_safe, details={"categories": str(response.categories_analysis)})
    except ImportError:
        logger.warning("azure-ai-contentsafety がインストールされていません")
        if _content_safety_required():
            return ShieldResult(is_safe=False, details={"reason": "client_unavailable"})
        return ShieldResult(is_safe=True, details={"reason": "client_unavailable"})
    except Exception:
        logger.exception("Prompt Shield チェックでエラーが発生")
        # fail-closed: チェック不能時は安全側に倒す
        return ShieldResult(is_safe=False, details={"reason": "check_failed"})


async def analyze_content(text: str) -> SafetyScores:
    """Text Analysis で出力コンテンツをチェックする（層4）"""
    endpoint = os.environ.get("CONTENT_SAFETY_ENDPOINT", "")
    if not endpoint:
        if _content_safety_required():
            logger.error("CONTENT_SAFETY_ENDPOINT が未設定のため Text Analysis をブロック")
            return SafetyScores(check_failed=True)
        logger.warning("CONTENT_SAFETY_ENDPOINT が未設定のため Text Analysis をスキップ")
        return SafetyScores()

    try:
        from azure.ai.contentsafety import ContentSafetyClient
        from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
        from azure.identity import DefaultAzureCredential

        client = ContentSafetyClient(
            endpoint=endpoint,
            credential=DefaultAzureCredential(),
        )
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
    except ImportError:
        logger.warning("azure-ai-contentsafety がインストールされていません")
        if _content_safety_required():
            return SafetyScores(check_failed=True)
        return SafetyScores()
    except Exception:
        logger.exception("Text Analysis でエラーが発生")
        return SafetyScores(check_failed=True)
