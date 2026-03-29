"""Content Safety ミドルウェアのテスト"""

import pytest

from src.middleware import SafetyScores, ShieldResult, analyze_content, check_prompt_shield


class TestShieldResult:
    """ShieldResult データクラスのテスト"""

    def test_safe_result(self):
        result = ShieldResult(is_safe=True)
        assert result.is_safe is True
        assert result.details is None

    def test_unsafe_result_with_details(self):
        result = ShieldResult(is_safe=False, details={"reason": "jailbreak"})
        assert result.is_safe is False
        assert result.details["reason"] == "jailbreak"


class TestSafetyScores:
    """SafetyScores データクラスのテスト"""

    def test_default_scores_are_zero(self):
        scores = SafetyScores()
        assert scores.hate == 0
        assert scores.self_harm == 0
        assert scores.sexual == 0
        assert scores.violence == 0

    def test_custom_scores(self):
        scores = SafetyScores(hate=2, violence=1)
        assert scores.hate == 2
        assert scores.violence == 1
        assert scores.sexual == 0


class TestCheckPromptShield:
    """Prompt Shield チェック関数のテスト"""

    @pytest.mark.asyncio
    async def test_returns_safe_when_endpoint_not_set(self, monkeypatch):
        """CONTENT_SAFETY_ENDPOINT 未設定時は is_safe=True を返す（開発環境用）"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        result = await check_prompt_shield("normal input")
        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_accepts_string_input(self):
        """文字列入力を受け付けること"""
        result = await check_prompt_shield("テスト入力")
        assert isinstance(result, ShieldResult)


class TestAnalyzeContent:
    """Text Analysis チェック関数のテスト"""

    @pytest.mark.asyncio
    async def test_returns_zero_scores_when_endpoint_not_set(self, monkeypatch):
        """CONTENT_SAFETY_ENDPOINT 未設定時はスコア0を返す"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        scores = await analyze_content("safe text")
        assert scores.hate == 0
        assert scores.self_harm == 0
        assert scores.sexual == 0
        assert scores.violence == 0

    @pytest.mark.asyncio
    async def test_returns_safety_scores_type(self):
        """SafetyScores 型を返すこと"""
        scores = await analyze_content("test")
        assert isinstance(scores, SafetyScores)
