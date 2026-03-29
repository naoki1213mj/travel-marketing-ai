"""Content Safety ミドルウェアのテスト"""

import pytest

from src.middleware import SafetyScores, ShieldResult, analyze_content, check_prompt_shield, check_tool_response


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
    async def test_returns_safe_when_endpoint_not_set_in_development(self, monkeypatch):
        """開発環境では CONTENT_SAFETY_ENDPOINT 未設定時に is_safe=True を返す"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        result = await check_prompt_shield("normal input")
        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_returns_unsafe_when_endpoint_not_set_in_production(self, monkeypatch):
        """本番環境では CONTENT_SAFETY_ENDPOINT 未設定時にブロックする"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        result = await check_prompt_shield("normal input")
        assert result.is_safe is False
        assert result.details == {"reason": "missing_endpoint"}

    @pytest.mark.asyncio
    async def test_accepts_string_input(self):
        """文字列入力を受け付けること"""
        result = await check_prompt_shield("テスト入力")
        assert isinstance(result, ShieldResult)


class TestAnalyzeContent:
    """Text Analysis チェック関数のテスト"""

    @pytest.mark.asyncio
    async def test_returns_zero_scores_when_endpoint_not_set_in_development(self, monkeypatch):
        """開発環境では CONTENT_SAFETY_ENDPOINT 未設定時にスコア0を返す"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        scores = await analyze_content("safe text")
        assert scores.hate == 0
        assert scores.self_harm == 0
        assert scores.sexual == 0
        assert scores.violence == 0
        assert scores.check_failed is False


class TestCheckToolResponse:
    """ツール応答 Prompt Shield チェック関数のテスト（層3）"""

    @pytest.mark.asyncio
    async def test_returns_safe_when_endpoint_not_set_in_development(self, monkeypatch):
        """開発環境では CONTENT_SAFETY_ENDPOINT 未設定時に is_safe=True を返す"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        result = await check_tool_response("web search result: normal content")
        assert result.is_safe is True

    @pytest.mark.asyncio
    async def test_returns_unsafe_when_endpoint_not_set_in_production(self, monkeypatch):
        """本番環境では CONTENT_SAFETY_ENDPOINT 未設定時にブロックする"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        result = await check_tool_response("tool output")
        assert result.is_safe is False

    @pytest.mark.asyncio
    async def test_accepts_long_text(self, monkeypatch):
        """長いテキストも受け付けること（4000文字に切り詰め）"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "development")
        long_text = "x" * 10000
        result = await check_tool_response(long_text)
        assert isinstance(result, ShieldResult)

    @pytest.mark.asyncio
    async def test_returns_check_failed_when_endpoint_not_set_in_production(self, monkeypatch):
        """本番環境では CONTENT_SAFETY_ENDPOINT 未設定時に check_failed=True を返す"""
        monkeypatch.delenv("CONTENT_SAFETY_ENDPOINT", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "production")
        scores = await analyze_content("safe text")
        assert scores.check_failed is True

    @pytest.mark.asyncio
    async def test_returns_safety_scores_type(self):
        """SafetyScores 型を返すこと"""
        scores = await analyze_content("test")
        assert isinstance(scores, SafetyScores)
