"""エージェントのツール関数テスト"""

import json

import pytest

from src.agents.data_search import search_customer_reviews, search_sales_history
from src.agents.marketing_plan import search_market_trends
from src.agents.regulation_check import check_ng_expressions, check_travel_law_compliance, search_safety_info


class TestDataSearchTools:
    """Agent1 のデータ検索ツールテスト"""

    @pytest.mark.asyncio
    async def test_search_sales_history_returns_json(self):
        """販売履歴検索が JSON 文字列を返すこと"""
        result = await search_sales_history(query="沖縄")
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_search_sales_history_filter_by_season(self):
        """季節フィルタが動作すること"""
        result = await search_sales_history(query="test", season="spring")
        parsed = json.loads(result)
        for item in parsed:
            assert item.get("season") == "spring"

    @pytest.mark.asyncio
    async def test_search_customer_reviews_returns_json(self):
        """顧客レビュー検索が JSON 文字列を返すこと"""
        result = await search_customer_reviews()
        parsed = json.loads(result)
        assert isinstance(parsed, list)

    @pytest.mark.asyncio
    async def test_search_customer_reviews_filter_by_rating(self):
        """最低評価フィルタが動作すること"""
        result = await search_customer_reviews(min_rating=4)
        parsed = json.loads(result)
        for item in parsed:
            assert item.get("rating", 0) >= 4


class TestRegulationCheckTools:
    """Agent3 の規制チェックツールテスト"""

    @pytest.mark.asyncio
    async def test_check_ng_expressions_detects_violation(self):
        """NG 表現が検出されること"""
        result = await check_ng_expressions("このプランは最安値です")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) > 0
        assert parsed[0]["expression"] == "最安値"

    @pytest.mark.asyncio
    async def test_check_ng_expressions_no_violation(self):
        """NG 表現がない場合は検出なしメッセージを返すこと"""
        result = await check_ng_expressions("安全な旅行プランです")
        assert "検出されませんでした" in result

    @pytest.mark.asyncio
    async def test_check_travel_law_compliance_returns_json(self):
        """旅行業法チェックが JSON 文字列を返すこと"""
        result = await check_travel_law_compliance("旅行業者の登録番号: 東京都知事登録旅行業第1234号")
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 5  # 5 項目のチェックリスト

    @pytest.mark.asyncio
    async def test_check_travel_law_compliance_detects_keyword(self):
        """キーワードが含まれていれば適合判定されること"""
        result = await check_travel_law_compliance("書面交付義務を遵守しています。広告表示規制に準拠。")
        parsed = json.loads(result)
        # 少なくとも 1 つは「適合」判定されるはず
        statuses = [item["status"] for item in parsed]
        assert "✅ 適合" in statuses


class TestConfigSettings:
    """設定ロードのテスト"""

    def test_get_settings_returns_dict(self):
        from src.config import get_settings

        settings = get_settings()
        assert "project_endpoint" in settings
        assert "model_name" in settings


class TestMarketingPlanTools:
    """Agent2 の市場トレンドツールテスト"""

    @pytest.mark.asyncio
    async def test_search_market_trends_returns_string(self):
        """市場トレンド検索が文字列を返すこと"""
        result = await search_market_trends(query="沖縄旅行 トレンド 2026")
        assert isinstance(result, str)
        assert "トレンド" in result

    @pytest.mark.asyncio
    async def test_search_market_trends_contains_info(self):
        """市場トレンド検索が情報を含むこと"""
        result = await search_market_trends(query="test")
        assert len(result) > 50


class TestSafetyInfoTool:
    """Agent3 の安全情報ツールテスト"""

    @pytest.mark.asyncio
    async def test_search_safety_info_returns_json(self):
        """安全情報検索が JSON 文字列を返すこと"""
        result = await search_safety_info(destination="沖縄")
        parsed = json.loads(result)
        assert parsed["destination"] == "沖縄"
        assert "safety_level" in parsed

    @pytest.mark.asyncio
    async def test_search_safety_info_different_destination(self):
        """異なる目的地でも動作すること"""
        result = await search_safety_info(destination="バリ島")
        parsed = json.loads(result)
        assert parsed["destination"] == "バリ島"

    def test_default_model_name(self, monkeypatch):
        """MODEL_NAME 未設定時のデフォルト値"""
        monkeypatch.delenv("MODEL_NAME", raising=False)
        from src.config import get_settings

        settings = get_settings()
        assert settings["model_name"] == "gpt-5-4-mini"
