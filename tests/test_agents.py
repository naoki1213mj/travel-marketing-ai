"""エージェントのツール関数テスト"""

import asyncio
import contextvars
import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock

import openai
import pytest
from openai import APIConnectionError, RateLimitError

from src import config as config_module
from src.agents.data_search import search_customer_reviews, search_sales_history
from src.agents.regulation_check import check_ng_expressions, check_travel_law_compliance, search_knowledge_base
from src.tool_telemetry import tool_event_context


def _disable_azd_env(monkeypatch) -> None:
    """テスト中は実マシンの azd env を参照しない。"""
    monkeypatch.setattr(config_module, "_get_azd_env_values", lambda: {})


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
    async def test_search_sales_history_emits_evidence_and_chart(self):
        """販売履歴検索は Fabric/local evidence と chart を tool_event に追加する"""
        events = []

        with tool_event_context(events.append, agent_name="data-search-agent", step=1):
            await search_sales_history(query="沖縄", season="spring")

        evidence_events = [event for event in events if event.get("tool") == "search_sales_history" and event.get("evidence")]
        assert evidence_events
        assert evidence_events[0]["evidence"][0]["source"] in {"fabric", "local"}
        assert evidence_events[0]["charts"][0]["chart_type"] == "bar"

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

    @pytest.mark.asyncio
    async def test_query_fabric_falls_back_on_credential_error(self, monkeypatch):
        """Fabric SQL のトークン取得失敗時も CSV フォールバックに流せる"""
        from azure.identity import CredentialUnavailableError

        import src.agents.data_search as ds

        class DummyCredential:
            def get_token(self, _scope):
                raise CredentialUnavailableError("credential unavailable")

        monkeypatch.setattr(ds, "_HAS_PYODBC", True)
        monkeypatch.setattr(ds, "DefaultAzureCredential", DummyCredential)
        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_sql_endpoint": "test.sql.fabric.microsoft.com"})

        result = ds._query_fabric("SELECT 1")
        assert result == []

    def test_query_fabric_uses_configured_lakehouse_database(self, monkeypatch):
        """Fabric SQL fallback は移行先 Lakehouse database 名を環境設定から使う。"""
        import src.agents.data_search as ds

        captured: dict[str, str] = {}

        class DummyToken:
            token = "token"

        class DummyCredential:
            def get_token(self, _scope):
                return DummyToken()

        class DummyCursor:
            description = [("value",)]

            def execute(self, query, params=None):
                captured["query"] = query
                captured["params"] = str(params)

            def fetchall(self):
                return [(1,)]

            def close(self):
                captured["cursor_closed"] = "true"

        class DummyConnection:
            def cursor(self):
                return DummyCursor()

            def close(self):
                captured["connection_closed"] = "true"

        def fake_connect(connection_string, attrs_before):
            captured["connection_string"] = connection_string
            captured["attrs_before"] = str(bool(attrs_before))
            return DummyConnection()

        monkeypatch.setattr(ds, "_HAS_PYODBC", True)
        monkeypatch.setattr(ds, "DefaultAzureCredential", DummyCredential)
        monkeypatch.setattr(ds, "pyodbc", SimpleNamespace(connect=fake_connect))
        monkeypatch.setattr(
            ds,
            "get_settings",
            lambda: {
                "fabric_sql_endpoint": "new.sql.fabric.microsoft.com",
                "fabric_lakehouse_database": "Travel_Lakehouse_v2",
            },
        )

        result = ds._query_fabric("SELECT 1")

        assert result == [{"value": 1}]
        assert "Server=new.sql.fabric.microsoft.com;" in captured["connection_string"]
        assert "Database=Travel_Lakehouse_v2;" in captured["connection_string"]
        assert captured["cursor_closed"] == "true"
        assert captured["connection_closed"] == "true"

    def test_fabric_queries_use_configured_table_names(self, monkeypatch):
        """移行先 Fabric workspace の table 名を設定で切り替えられる。"""
        import src.agents.data_search as ds

        captured: list[str] = []

        monkeypatch.setattr(
            ds,
            "get_settings",
            lambda: {
                "fabric_sales_table": "travel_sales",
                "fabric_reviews_table": "travel_review",
            },
        )
        monkeypatch.setattr(ds, "_fabric_table_columns", lambda table_name: set())
        monkeypatch.setattr(ds, "_query_fabric", lambda query, params=None: captured.append(query) or [])

        ds._get_sales_data_from_fabric()
        ds._get_reviews_from_fabric()

        assert "FROM travel_sales" in captured[0]
        assert "FROM travel_review" in captured[1]

    def test_fabric_sales_query_supports_ws3iq_schema(self, monkeypatch):
        """ws-3iq-demo の販売 table schema を既存出力 schema に正規化する。"""
        import src.agents.data_search as ds

        captured: dict[str, object] = {}

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_sales_table": "travel_sales"})
        monkeypatch.setattr(
            ds,
            "_fabric_table_columns",
            lambda table_name: {"travel_destination", "date", "price", "number_of_people", "age_group"},
        )

        def fake_query(query, params=None):
            captured["query"] = query
            captured["params"] = params
            return [{"plan_name": "京都 2泊3日", "destination": "京都", "season": "winter"}]

        monkeypatch.setattr(ds, "_query_fabric", fake_query)

        result = ds._get_sales_data_from_fabric(season="winter", region="京都")

        assert result[0]["plan_name"] == "京都 2泊3日"
        assert "Travel_destination AS destination" in str(captured["query"])
        assert "TRY_CONVERT(date, [Date], 111)" in str(captured["query"])
        assert "SUM(CAST(Price AS BIGINT)) AS revenue" in str(captured["query"])
        assert captured["params"] == ["%京都%", 12, 1, 2]

    def test_fabric_reviews_query_supports_ws3iq_schema(self, monkeypatch):
        """ws-3iq-demo のレビュー table schema を既存出力 schema に正規化する。"""
        import src.agents.data_search as ds

        captured: dict[str, object] = {}

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_reviews_table": "travel_review"})
        monkeypatch.setattr(ds, "_fabric_table_columns", lambda table_name: {"travel_destination", "rating", "comments"})

        def fake_query(query, params=None):
            captured["query"] = query
            captured["params"] = params
            return [{"plan_name": "京都", "rating": 3, "comment": "寺社仏閣が素晴らしかった"}]

        monkeypatch.setattr(ds, "_query_fabric", fake_query)

        result = ds._get_reviews_from_fabric(plan_name="京都", min_rating=3)

        assert result[0]["comment"] == "寺社仏閣が素晴らしかった"
        assert "Travel_destination AS plan_name" in str(captured["query"])
        assert "Comments AS comment" in str(captured["query"])
        assert captured["params"] == ["%京都%", 3]

    @pytest.mark.asyncio
    async def test_query_data_agent_uses_fabric_sql_fallback(self, monkeypatch):
        """Data Agent endpoint 不可時も Fabric SQL で分析できれば local 扱いにしない。"""
        import src.agents.data_search as ds

        async def unavailable_data_agent(question: str) -> None:
            return None

        monkeypatch.setattr(ds, "_query_data_agent", unavailable_data_agent)
        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "京都 2泊3日",
                    "destination": "京都",
                    "season": "winter",
                    "revenue": 64000,
                    "pax": 2,
                    "customer_segment": "20代",
                    "booking_count": 1,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "京都", "rating": 3, "comment": "寺社仏閣が素晴らしかった"}],
        )

        result = await ds.query_data_agent("人気の旅行先を教えて")
        parsed = json.loads(result)

        assert parsed["source"] == "Fabric SQL fallback"
        assert "京都 2泊3日" in parsed["answer"]
        assert "ws-3iq-demo Lakehouse" in parsed["answer"]

    @pytest.mark.asyncio
    async def test_query_data_agent_uses_fabric_sql_primary_when_rest_disabled(self, monkeypatch):
        """Data Agent REST 無効時は不安定な preview 経路を呼ばず SQL 分析を primary にする。"""
        import src.agents.data_search as ds

        async def unexpected_data_agent(question: str) -> str:
            raise AssertionError("Data Agent REST should be skipped")

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "sql"})
        monkeypatch.setattr(ds, "_query_data_agent", unexpected_data_agent)
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "沖縄 2泊3日",
                    "destination": "沖縄",
                    "season": "spring",
                    "revenue": 1022000,
                    "pax": 17,
                    "customer_segment": "30代",
                    "booking_count": 6,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "沖縄", "rating": 5, "comment": "海がとても綺麗でした！"}],
        )

        result = await ds.query_data_agent("春の沖縄ファミリー向け施策を分析して")
        parsed = json.loads(result)

        assert parsed["source"] == "Fabric SQL primary"
        assert "Fabric Data Agent REST 経路は preview" in parsed["answer"]
        assert "沖縄 2泊3日" in parsed["answer"]

    @pytest.mark.asyncio
    async def test_query_data_agent_supplements_low_confidence_answer_with_fabric_sql(self, monkeypatch):
        """Data Agent が弱い回答を返した場合は Fabric SQL の具体データで補強する。"""
        import src.agents.data_search as ds

        async def weak_data_agent(question: str) -> str:
            return "指定された条件で売上トレンドやレビュー評価の詳細は提示できませんでした。必要であれば追加提示してください。"

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        monkeypatch.setattr(ds, "_query_data_agent", weak_data_agent)
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "沖縄 2泊3日",
                    "destination": "沖縄",
                    "season": "spring",
                    "revenue": 1022000,
                    "pax": 16,
                    "customer_segment": "30代",
                    "booking_count": 8,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "沖縄", "rating": 5, "comment": "シュノーケリングが最高"}],
        )

        result = await ds.query_data_agent("春の沖縄ファミリー向け施策を分析して")
        parsed = json.loads(result)

        assert parsed["source"] == "Fabric Data Agent + Fabric SQL"
        assert "回答が十分な具体データを含まなかった" in parsed["answer"]
        assert "沖縄 2泊3日" in parsed["answer"]
        assert "1,022,000 円" in parsed["answer"]

    def test_fabric_sql_analysis_uses_region_and_season_from_question(self, monkeypatch):
        """Data Agent 補強用 SQL は質問文の地域・季節を反映する。"""
        import src.agents.data_search as ds

        captured: dict[str, object] = {}

        def fake_sales(**kwargs):
            captured["sales_kwargs"] = kwargs
            return [
                {
                    "plan_name": "沖縄 3泊4日",
                    "destination": "沖縄",
                    "season": "spring",
                    "revenue": 929000,
                    "pax": 12,
                    "customer_segment": "30代",
                    "booking_count": 4,
                }
            ]

        def fake_reviews(**kwargs):
            captured["review_kwargs"] = kwargs
            return [{"plan_name": "沖縄", "rating": 5, "comment": "リゾート気分を満喫"}]

        monkeypatch.setattr(ds, "_get_sales_data_from_fabric", fake_sales)
        monkeypatch.setattr(ds, "_get_reviews_from_fabric", fake_reviews)

        answer = ds._build_fabric_sql_analysis("春の沖縄ファミリー施策を分析して")

        assert captured["sales_kwargs"] == {"season": "spring", "region": "沖縄"}
        assert captured["review_kwargs"] == {"plan_name": "沖縄"}
        assert "適用フィルタ: 地域=沖縄, 季節=spring" in str(answer)
        assert "沖縄 3泊4日" in str(answer)

    def test_data_agent_question_contains_demo_business_semantics(self):
        """Data Agent への質問にはデモ用の業務語変換ルールを含める。"""
        import src.agents.data_search as ds

        prompt = ds._build_data_agent_question("春の沖縄ファミリー施策を分析して")

        assert "Number_of_people >= 3" in prompt
        assert "yyyy/MM/dd" in prompt
        assert "Category は 国内/海外" in prompt
        assert "「学生」は Age_group が 20代" in prompt
        assert "Age_group が 30代/40代" in prompt
        assert "SUM(Price)" in prompt
        assert "Rating 分布" in prompt
        assert "X/XX/XXX" in prompt

    def test_low_confidence_data_agent_answer_detection(self):
        """Data Agent の回答不能文は、数字を含んでいても低信頼として扱う。"""
        import src.agents.data_search as ds

        weak_answer = "30代ファミリー向けの売上トレンドは提示できませんでした。必要であれば追加提示してください。"
        technical_failure_answer = "Lakehouse上で該当クエリを実行しましたが、技術的な理由（内部エラー）により必要なデータを取得できませんでした。"
        placeholder_answer = "合計売上は ¥X,XXX,XXX、予約件数は XX件です。以下は分析例です。"
        missing_sales_answer = "売上上位・合計売上・予約件数の具体的数値はデータ不足のためデータなしです。レビュー件数は18件です。"
        missing_sales_variant = "売上上位プラン、合計売上金額、予約件数に該当するデータが存在しませんでした。"
        concrete_answer = "沖縄 2泊3日が売上上位。合計売上 1,022,000 円、予約 8 件。"

        assert ds._is_low_confidence_data_agent_answer(weak_answer) is True
        assert ds._is_low_confidence_data_agent_answer(technical_failure_answer) is True
        assert ds._is_low_confidence_data_agent_answer(placeholder_answer) is True
        assert ds._is_low_confidence_data_agent_answer(missing_sales_answer) is True
        assert ds._is_low_confidence_data_agent_answer(missing_sales_variant) is True
        assert ds._is_low_confidence_data_agent_answer(concrete_answer) is False

    def test_extract_data_agent_tool_outputs_prefers_execute_results(self):
        """Data Agent run steps から実行結果 tool output を抽出する。"""
        import src.agents.data_search as ds

        steps = SimpleNamespace(
            data=[
                SimpleNamespace(
                    step_details=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="analyze.database.fewshots.loading",
                                    output="Loaded 0 fewshots",
                                )
                            )
                        ]
                    )
                ),
                SimpleNamespace(
                    step_details=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                function=SimpleNamespace(
                                    name="analyze.database.execute",
                                    output="| destination | revenue |\n| 沖縄 | 1022000 |",
                                )
                            )
                        ]
                    )
                ),
            ]
        )

        assert ds._extract_data_agent_tool_outputs(steps) == ["| destination | revenue | | 沖縄 | 1022000 |"]

    def test_fabric_table_names_reject_invalid_identifiers(self, monkeypatch):
        """Fabric table 名は SQL injection にならない identifier だけ許可する。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_sales_table": "travel_sales;DROP TABLE x"})

        assert ds._fabric_table_name("fabric_sales_table", "sales_results") == "sales_results"

    def test_resolve_fabric_data_agent_runtime_defaults_to_sql(self, monkeypatch):
        """Data Agent REST preview は明示 opt-in のときだけ使う。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(ds, "get_settings", lambda: {})
        assert ds._resolve_fabric_data_agent_runtime() == "sql"

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        assert ds._resolve_fabric_data_agent_runtime() == "rest"


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
    async def test_local_regulation_checks_emit_evidence(self):
        """ローカル規制チェックは evidence / chart を tool_event に追加する"""
        events = []

        with tool_event_context(events.append, agent_name="regulation-check-agent", step=4):
            await check_travel_law_compliance("書面交付義務を遵守しています。")

        evidence_events = [
            event for event in events if event.get("tool") == "check_travel_law_compliance" and event.get("evidence")
        ]
        assert evidence_events
        assert evidence_events[0]["evidence"][0]["source"] == "local-check"
        assert evidence_events[0]["charts"][0]["chart_type"] == "table"

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


class TestKnowledgeBaseTool:
    """Agent3 のナレッジベース検索ツールテスト"""

    @pytest.mark.asyncio
    async def test_search_knowledge_base_returns_json(self):
        """ナレッジベース検索がフォールバック JSON を返すこと"""
        result = await search_knowledge_base(query="景品表示法")
        parsed = json.loads(result)
        assert "query" in parsed
        assert parsed["query"] == "景品表示法"

    @pytest.mark.asyncio
    async def test_search_knowledge_base_contains_regulations(self):
        """フォールバック時に NG 表現リストが含まれること"""
        result = await search_knowledge_base(query="旅行業法")
        parsed = json.loads(result)
        assert "ng_expressions" in parsed or "results" in parsed

    @pytest.mark.asyncio
    async def test_search_knowledge_base_different_query(self):
        """異なるクエリでも動作すること"""
        result = await search_knowledge_base(query="広告規制")
        parsed = json.loads(result)
        assert "query" in parsed
        assert parsed["query"] == "広告規制"

    @pytest.mark.asyncio
    async def test_search_knowledge_base_emits_fallback_evidence(self, monkeypatch):
        """Foundry IQ 未接続時も安全な fallback evidence を追加する"""
        import src.agents.regulation_check as rc

        monkeypatch.setattr(rc, "_get_search_credentials", lambda: ("", ""))
        events = []

        with tool_event_context(events.append, agent_name="regulation-check-agent", step=4):
            await search_knowledge_base(query="旅行業法")

        evidence_events = [event for event in events if event.get("tool") == "foundry_iq_search" and event.get("evidence")]
        assert evidence_events
        assert evidence_events[0]["evidence"][0]["source"] == "local-check"

    def test_default_model_name(self, monkeypatch):
        """MODEL_NAME 未設定時のデフォルト値"""
        monkeypatch.delenv("MODEL_NAME", raising=False)
        from src.config import get_settings

        settings = get_settings()
        assert settings["model_name"] == "gpt-5-4-mini"


class TestBrochureGenTools:
    """Agent4 のブローシャ生成ツールテスト"""

    @pytest.mark.asyncio
    async def test_generate_hero_image_fallback(self, monkeypatch):
        """OpenAI クライアント未初期化時にフォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        # シングルトンをリセットして未初期化にする
        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
        bg.set_current_conversation_id("hero-test")

        result = await bg.generate_hero_image(
            prompt="beautiful beach",
            destination="Okinawa",
            style="photorealistic",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "generated"
        assert parsed["type"] == "hero"
        # side-channel に保存されていること
        images = bg.pop_pending_images("hero-test")
        assert "hero" in images
        assert images["hero"].startswith("data:image/")

    @pytest.mark.asyncio
    async def test_generate_banner_image_fallback(self, monkeypatch):
        """OpenAI クライアント未初期化時にフォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
        bg.set_current_conversation_id("banner-test")

        result = await bg.generate_banner_image(
            prompt="travel banner",
            platform="instagram",
        )
        parsed = json.loads(result)
        assert parsed["status"] == "generated"
        assert parsed["platform"] == "instagram"
        assert parsed["size"] == "1024x1024"
        images = bg.pop_pending_images("banner-test")
        assert "banner_instagram" in images
        assert images["banner_instagram"].startswith("data:image/")

    @pytest.mark.asyncio
    async def test_generate_banner_image_twitter_size(self, monkeypatch):
        """twitter 指定は X 用バナーへ正規化される"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        result = await bg.generate_banner_image(
            prompt="travel banner",
            platform="twitter",
        )
        parsed = json.loads(result)
        assert parsed["size"] == "1536x1024"
        assert parsed["platform"] == "x"
        assert parsed["display_aspect_ratio"] == "1.91:1"

    @pytest.mark.asyncio
    async def test_generate_banner_image_uses_conversation_id_fallback(self, monkeypatch):
        """context が落ちても最後の conversation_id で side-channel 保存できる"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)
        monkeypatch.setattr(
            bg,
            "_conversation_id_var",
            contextvars.ContextVar("brochure_conversation_id_test", default=""),
        )
        monkeypatch.setattr(bg, "_conversation_id_fallback", "banner-fallback")

        await bg.generate_banner_image(prompt="travel banner", platform="instagram")

        images = bg.pop_pending_images("banner-fallback")
        assert "banner_instagram" in images
        assert images["banner_instagram"] == bg._FALLBACK_IMAGE

    def test_pop_pending_images_returns_and_clears(self):
        """pop_pending_images が保存済み画像を返しクリアする"""
        import src.agents.brochure_gen as bg

        bg._pending_images = {
            "test-conv": {"hero": "data:image/png;base64,abc", "banner_instagram": "data:image/png;base64,def"}
        }
        result = bg.pop_pending_images("test-conv")
        assert result == {"hero": "data:image/png;base64,abc", "banner_instagram": "data:image/png;base64,def"}
        assert "test-conv" not in bg._pending_images

    def test_pop_pending_images_empty(self):
        """画像がない場合は空辞書を返す"""
        import src.agents.brochure_gen as bg

        bg._pending_images = {}
        result = bg.pop_pending_images("nonexistent")
        assert result == {}

    def test_pop_pending_video_job_returns_and_clears(self):
        """pop_pending_video_job がジョブ情報を返しクリアする"""
        import src.agents.video_gen as vg

        vg._pending_video_jobs = {"video-test": {"job_id": "promo-123", "status": "submitted"}}
        result = vg.pop_pending_video_job("video-test")
        assert result == {"job_id": "promo-123", "status": "submitted"}
        assert vg._pending_video_jobs == {}

    def test_pop_pending_video_job_none(self):
        """ジョブがない場合は None を返す"""
        import src.agents.video_gen as vg

        vg._pending_video_jobs = {}
        result = vg.pop_pending_video_job("missing")
        assert result is None

    def test_get_gpt_image_client_no_endpoint(self, monkeypatch):
        """project_endpoint 未設定時は None を返す"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

        result = bg._get_gpt_image_client()
        assert result is None
        assert bg._gpt_image_clients == {}

    def test_get_gpt_image_client_does_not_cache_transient_init_failure(self, monkeypatch):
        """初期化失敗はキャッシュせず、次回の成功を許可する"""
        import src.agents.brochure_gen as bg

        calls = {"count": 0}

        class _FakeAzureOpenAI:
            def __init__(self, **kwargs):
                calls["count"] += 1
                if calls["count"] == 1:
                    raise OSError("temporary init failure")
                self.kwargs = kwargs

        monkeypatch.setattr(bg, "_gpt_image_clients", {})
        monkeypatch.setattr(openai, "AzureOpenAI", _FakeAzureOpenAI)
        monkeypatch.setattr(bg, "get_shared_credential", lambda: object())
        monkeypatch.setattr(bg, "get_bearer_token_provider", lambda credential, scope: "token-provider")

        first = bg._get_gpt_image_client("https://example.services.ai.azure.com")
        second = bg._get_gpt_image_client("https://example.services.ai.azure.com")

        assert first is None
        assert isinstance(second, _FakeAzureOpenAI)
        assert calls["count"] == 2
        assert bg._gpt_image_clients["https://example.services.ai.azure.com"] is second

    def test_get_gpt_image_client_cached(self, monkeypatch):
        """2 回目以降はキャッシュされた結果を返す"""
        import src.agents.brochure_gen as bg

        monkeypatch.setattr(bg, "_gpt_image_clients", {"https://example.services.ai.azure.com": "cached-client"})

        result = bg._get_gpt_image_client("https://example.services.ai.azure.com")
        assert result == "cached-client"

    def test_resolve_ai_account_endpoint_strips_project_path(self):
        """project endpoint から account endpoint を抽出できる"""
        import src.agents.brochure_gen as bg

        assert (
            bg._resolve_ai_account_endpoint("https://example.services.ai.azure.com/api/projects/demo")
            == "https://example.services.ai.azure.com"
        )

    def test_resolve_gpt_image_deployment_uses_override(self, monkeypatch):
        """GPT Image 2 の deployment 名は環境変数で上書きできる"""
        import src.agents.brochure_gen as bg

        monkeypatch.setenv("GPT_IMAGE_2_DEPLOYMENT_NAME", "gpt-image-2-custom")

        assert bg._resolve_gpt_image_deployment("gpt-image-2") == "gpt-image-2-custom"

    @pytest.mark.asyncio
    async def test_generate_image_returns_fallback_on_no_client(self, monkeypatch):
        """クライアントが None の場合フォールバック画像を返す"""
        import src.agents.brochure_gen as bg

        monkeypatch.setattr(
            bg,
            "_get_gpt_image_client",
            lambda _account_endpoint=None: None,
        )
        monkeypatch.setattr(
            bg,
            "get_settings",
            lambda: {
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "gpt_image_15_deployment_name": "gpt-image-1.5",
                "gpt_image_2_deployment_name": "gpt-image-2",
            },
        )
        bg.set_current_image_settings({"image_model": bg._DEFAULT_IMAGE_MODEL, "image_quality": "medium"})

        result = await bg._generate_image("test prompt")
        assert result == bg._FALLBACK_IMAGE

    @pytest.mark.asyncio
    async def test_generate_image_returns_fallback_on_exception(self, monkeypatch):
        """画像生成中にエラーが発生した場合フォールバック画像を返す"""

        import src.agents.brochure_gen as bg

        mock_client = MagicMock()
        mock_client.images.generate.side_effect = Exception("API error")
        monkeypatch.setattr(
            bg,
            "_get_gpt_image_client",
            lambda _account_endpoint=None: mock_client,
        )
        monkeypatch.setattr(
            bg,
            "get_settings",
            lambda: {
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "gpt_image_15_deployment_name": "gpt-image-1.5",
                "gpt_image_2_deployment_name": "gpt-image-2",
            },
        )
        bg.set_current_image_settings({"image_model": bg._DEFAULT_IMAGE_MODEL, "image_quality": "medium"})

        result = await bg._generate_image("test prompt")
        assert result == bg._FALLBACK_IMAGE

    @pytest.mark.asyncio
    async def test_generate_image_uses_selected_gpt_image_deployment(self, monkeypatch):
        """gpt-image-2 選択時は対応する deployment へ切り替える"""
        import src.agents.brochure_gen as bg

        class _ResponseItem:
            b64_json = "abc123"

        class _Response:
            data = [_ResponseItem()]

        captured: dict[str, object] = {}
        mock_client = MagicMock()
        mock_client.images.generate.return_value = _Response()

        monkeypatch.setattr(
            bg,
            "get_settings",
            lambda: {
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "gpt_image_15_deployment_name": "gpt-image-1.5",
                "gpt_image_2_deployment_name": "gpt-image-2-custom",
            },
        )
        bg.set_current_image_settings({"image_model": "gpt-image-2", "image_quality": "high"})

        def _fake_get_client(account_endpoint: str | None = None):
            captured["account_endpoint"] = account_endpoint
            return mock_client

        monkeypatch.setattr(bg, "_get_gpt_image_client", _fake_get_client)

        result = await bg._generate_image("test prompt")

        assert result == "data:image/png;base64,abc123"
        assert captured["account_endpoint"] == "https://example.services.ai.azure.com"
        mock_client.images.generate.assert_called_once_with(
            model="gpt-image-2-custom",
            prompt="test prompt",
            n=1,
            size="1024x1024",
            quality="high",
            output_format="png",
        )

    def test_extract_retry_after_seconds_returns_float(self):
        """Retry-After ヘッダを秒数へ変換できる"""
        import src.agents.brochure_gen as bg

        assert bg._extract_retry_after_seconds({"Retry-After": "3"}) == 3.0
        assert bg._extract_retry_after_seconds({"Retry-After": "-1"}) is None
        assert bg._extract_retry_after_seconds({}) is None

    def test_compute_gpt_retry_delay_prefers_retry_after(self):
        """GPT 画像生成 retry は Retry-After を優先する"""
        import src.agents.brochure_gen as bg

        response = MagicMock()
        response.headers = {"Retry-After": "4"}
        exc = RateLimitError("rate limited", response=response, body=None)

        assert bg._compute_gpt_retry_delay(exc, 2) == 4.0

    def test_compute_gpt_retry_delay_caps_retry_after(self):
        """極端な Retry-After は UI 待機を長時間ブロックしないよう上限をかける"""
        import src.agents.brochure_gen as bg

        response = MagicMock()
        response.headers = {"Retry-After": "999"}
        exc = RateLimitError("rate limited", response=response, body=None)

        assert bg._compute_gpt_retry_delay(exc, 2) == bg._GPT_IMAGE_MAX_RETRY_DELAY_SECONDS

    @pytest.mark.asyncio
    async def test_generate_image_gpt_retries_on_rate_limit(self, monkeypatch):
        """GPT 画像生成は 429 の一時失敗時に再試行する"""
        import src.agents.brochure_gen as bg

        class _ResponseItem:
            b64_json = "abc123"

        class _Response:
            data = [_ResponseItem()]

        response = MagicMock()
        response.headers = {"Retry-After": "0"}
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = [
            RateLimitError("rate limited", response=response, body=None),
            _Response(),
        ]

        monkeypatch.setattr(
            bg,
            "_get_gpt_image_client",
            lambda _account_endpoint=None: mock_client,
        )
        monkeypatch.setattr(
            bg,
            "get_settings",
            lambda: {
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "gpt_image_15_deployment_name": "gpt-image-1.5",
                "gpt_image_2_deployment_name": "gpt-image-2",
            },
        )
        bg.set_current_image_settings({"image_model": "gpt-image-2", "image_quality": "medium"})

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(bg.asyncio, "sleep", _fake_sleep)

        result = await bg._generate_image("test prompt")

        assert result == "data:image/png;base64,abc123"
        assert sleep_calls == [1.0]
        assert mock_client.images.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_image_gpt_retries_on_connection_error(self, monkeypatch):
        """GPT 画像生成は接続エラー時も再試行する"""
        import src.agents.brochure_gen as bg

        class _ResponseItem:
            b64_json = "xyz789"

        class _Response:
            data = [_ResponseItem()]

        request = MagicMock()
        mock_client = MagicMock()
        mock_client.images.generate.side_effect = [
            APIConnectionError(message="temporary", request=request),
            _Response(),
        ]

        monkeypatch.setattr(
            bg,
            "_get_gpt_image_client",
            lambda _account_endpoint=None: mock_client,
        )
        monkeypatch.setattr(
            bg,
            "get_settings",
            lambda: {
                "project_endpoint": "https://example.services.ai.azure.com/api/projects/demo",
                "gpt_image_15_deployment_name": "gpt-image-1.5",
                "gpt_image_2_deployment_name": "gpt-image-2",
            },
        )
        bg.set_current_image_settings({"image_model": "gpt-image-2", "image_quality": "medium"})

        sleep_calls: list[float] = []

        async def _fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)

        monkeypatch.setattr(bg.asyncio, "sleep", _fake_sleep)

        result = await bg._generate_image("test prompt")

        assert result == "data:image/png;base64,xyz789"
        assert sleep_calls == [2.0]
        assert mock_client.images.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_generate_image_mai_retries_on_429(self, monkeypatch):
        """MAI 429 は Retry-After を尊重して再試行する"""
        import src.agents.brochure_gen as bg

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return json.dumps({"data": [{"b64_json": "mai-image"}]}).encode("utf-8")

        sleeps: list[float] = []
        attempts = {"count": 0}

        def _fake_urlopen(_request, timeout=0):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise urllib.error.HTTPError(
                    url="https://example.services.ai.azure.com/mai/v1/images/generations",
                    code=429,
                    msg="Too Many Requests",
                    hdrs={"Retry-After": "1"},
                    fp=io.BytesIO(b'{"error":"rate limit"}'),
                )
            return _Response()

        async def _fake_sleep(seconds: float):
            sleeps.append(seconds)

        monkeypatch.setattr(bg, "get_settings", lambda: {"image_project_endpoint_mai": "https://example.services.ai.azure.com"})
        monkeypatch.setattr(bg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(bg.urllib.request, "urlopen", _fake_urlopen)
        monkeypatch.setattr(bg.asyncio, "sleep", _fake_sleep)
        monkeypatch.setattr(bg, "_MAI_RATE_LIMIT_INTERVAL_SECONDS", 0.0)
        monkeypatch.setattr(bg, "_MAI_MAX_ATTEMPTS", 2)
        monkeypatch.setattr(bg, "_mai_request_lock", asyncio.Lock())
        monkeypatch.setattr(bg, "_mai_last_request_started_at", 0.0)

        result = await bg._generate_image_mai("test prompt", 1024, 1024)

        assert result == "data:image/png;base64,mai-image"
        assert attempts["count"] == 2
        assert sleeps == [1.0]

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_no_endpoint(self, monkeypatch):
        """CONTENT_UNDERSTANDING_ENDPOINT 未設定時に警告を返す"""
        # data/ ディレクトリ内のパスを使う（パストラバーサル防止ガードを通過させる）
        from pathlib import Path

        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        allowed_path = str(Path(bg.__file__).resolve().parent.parent.parent / "data" / "dummy.pdf")
        monkeypatch.delenv("CONTENT_UNDERSTANDING_ENDPOINT", raising=False)
        result = await bg.analyze_existing_brochure(allowed_path)
        assert "見つかりません" in result or "利用できません" in result

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_file_not_found(self, monkeypatch):
        """ファイルが見つからない場合のエラー"""
        from pathlib import Path

        import src.agents.brochure_gen as bg

        allowed_path = str(Path(bg.__file__).resolve().parent.parent.parent / "data" / "nonexistent.pdf")
        monkeypatch.setenv("CONTENT_UNDERSTANDING_ENDPOINT", "https://test.cognitiveservices.azure.com")
        result = await bg.analyze_existing_brochure(allowed_path)
        assert "見つかりません" in result

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_path_traversal(self):
        """パストラバーサル攻撃を拒否する"""
        import src.agents.brochure_gen as bg

        result = await bg.analyze_existing_brochure("../../etc/passwd")
        parsed = json.loads(result)
        assert "error" in parsed
        assert "許可されていません" in parsed["error"]

    @pytest.mark.asyncio
    async def test_analyze_existing_brochure_rejects_sibling_data_prefix(self, tmp_path, monkeypatch):
        """data と同じ prefix の sibling directory は許可しない"""
        import src.agents.brochure_gen as bg

        _disable_azd_env(monkeypatch)
        repo_root = tmp_path / "repo"
        allowed_dir = repo_root / "data"
        sibling_dir = repo_root / "data_evil"
        allowed_dir.mkdir(parents=True)
        sibling_dir.mkdir()
        sibling_pdf = sibling_dir / "brochure.pdf"
        sibling_pdf.write_bytes(b"%PDF-1.4")

        fake_module_file = repo_root / "src" / "agents" / "brochure_gen.py"
        fake_module_file.parent.mkdir(parents=True)
        fake_module_file.write_text("", encoding="utf-8")
        monkeypatch.setattr(bg, "__file__", str(fake_module_file))

        result = await bg.analyze_existing_brochure(str(sibling_pdf))
        parsed = json.loads(result)
        assert "error" in parsed
        assert "許可されていません" in parsed["error"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_no_endpoint(self, monkeypatch):
        """SPEECH_SERVICE_ENDPOINT 未設定時に unavailable を返す"""
        import src.agents.video_gen as vg

        monkeypatch.delenv("SPEECH_SERVICE_ENDPOINT", raising=False)
        monkeypatch.delenv("SPEECH_SERVICE_REGION", raising=False)
        result = await vg.generate_promo_video("テストサマリ", "concierge")
        parsed = json.loads(result)
        assert parsed["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_generate_promo_video_uses_hd_ssml_payload(self, monkeypatch):
        """動画生成は avatar 互換の簡素な SSML と既定 avatar 設定で送信する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-123"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.delenv("VIDEO_GEN_VOICE", raising=False)
        monkeypatch.delenv("VIDEO_GEN_AVATAR_CHARACTER", raising=False)
        monkeypatch.delenv("VIDEO_GEN_AVATAR_STYLE", raising=False)
        monkeypatch.delenv("VIDEO_GEN_BACKGROUND_COLOR", raising=False)
        monkeypatch.delenv("VIDEO_GEN_BITRATE_KBPS", raising=False)
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("春の北海道旅。温泉と絶景を楽しめます。", "concierge")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert parsed["job_id"] == "promo-job-123"
        assert payload["inputKind"] == "SSML"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lisa"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual-sitting"
        assert payload["avatarConfig"]["bitrateKbps"] == 4000
        ssml_content = payload["inputs"][0]["content"]
        assert "ja-JP-Nanami:DragonHDLatestNeural" in ssml_content
        assert "gesture.show-front-1" in ssml_content
        assert "詳しくはブローシャをご確認のうえ" in ssml_content
        assert "mstts:express-as" not in ssml_content
        assert "mstts:paralinguistic" not in ssml_content
        assert "<prosody" not in ssml_content
        assert "<emphasis" not in ssml_content

    @pytest.mark.asyncio
    async def test_generate_promo_video_guide_uses_valid_avatar_style(self, monkeypatch):
        """guide 指定時は Lori の有効な casual スタイルを使う"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-guide"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("沖縄の海と文化を体験できる旅です。", "guide")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lori"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual"
        assert "gesture.hello" in payload["inputs"][0]["content"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_respects_env_overrides(self, monkeypatch):
        """動画生成は env の voice / avatar / bitrate 上書きを反映する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-456"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setenv("VIDEO_GEN_VOICE", "ja-JP-Masaru:DragonHDLatestNeural")
        monkeypatch.setenv("VIDEO_GEN_AVATAR_CHARACTER", "meg")
        monkeypatch.setenv("VIDEO_GEN_AVATAR_STYLE", "business-standing")
        monkeypatch.setenv("VIDEO_GEN_BACKGROUND_COLOR", "#11223344")
        monkeypatch.setenv("VIDEO_GEN_BITRATE_KBPS", "5500")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("沖縄の海と文化を体験できる旅です。", "guide")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "meg"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "business-standing"
        assert payload["avatarConfig"]["backgroundColor"] == "#11223344"
        assert payload["avatarConfig"]["bitrateKbps"] == 5500
        assert "ja-JP-Masaru:DragonHDLatestNeural" in payload["inputs"][0]["content"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_falls_back_when_bitrate_is_invalid(self, monkeypatch):
        """bitrate の env が不正でも既定値で送信する"""
        import src.agents.video_gen as vg

        captured: dict[str, object] = {}

        class _Token:
            token = "test-token"

        class _Credential:
            def get_token(self, _scope: str) -> _Token:
                return _Token()

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return b'{"id": "promo-job-789"}'

        def _fake_urlopen(request, timeout: int = 0):
            del timeout
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return _Response()

        monkeypatch.setenv("SPEECH_SERVICE_ENDPOINT", "https://test.cognitiveservices.azure.com")
        monkeypatch.setenv("SPEECH_SERVICE_REGION", "eastus2")
        monkeypatch.setenv("VIDEO_GEN_BITRATE_KBPS", "invalid")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("札幌のグルメを巡る旅です。", "concierge")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["bitrateKbps"] == 4000

    def test_create_brochure_gen_agent_with_mock(self, monkeypatch):
        """ブローシャ生成エージェントが正しいツール数で作成されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.brochure_gen as bg

        mock_agent = MagicMock()
        mock_client = MagicMock()
        mock_client.as_agent.return_value = mock_agent

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        agent = bg.create_brochure_gen_agent()
        assert agent is mock_agent
        call_kwargs = mock_client.as_agent.call_args.kwargs
        assert call_kwargs["name"] == "brochure-gen-agent"
        assert len(call_kwargs["tools"]) == 3

    def test_create_brochure_gen_agent_with_model_settings(self, monkeypatch):
        """model_settings が agent_kwargs に反映されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.brochure_gen as bg

        mock_client = MagicMock()
        mock_client.as_agent.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        bg.create_brochure_gen_agent(model_settings={"temperature": 0.5, "max_tokens": 2000, "top_p": 0.9})
        call_kwargs = mock_client.as_agent.call_args.kwargs
        opts = call_kwargs["default_options"]
        assert opts["temperature"] == 0.5
        assert opts["max_output_tokens"] == 2000
        assert opts["top_p"] == 0.9


class TestMarketingPlanAgent:
    """Agent2 のマーケ施策エージェント作成テスト"""

    def test_create_marketing_plan_agent_with_mock(self, monkeypatch):
        """マーケ施策エージェントが作成されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.marketing_plan as mp

        mock_agent = MagicMock()
        mock_client = MagicMock()
        mock_client.as_agent.return_value = mock_agent
        mock_client.get_web_search_tool.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        agent = mp.create_marketing_plan_agent()
        assert agent is mock_agent
        call_kwargs = mock_client.as_agent.call_args.kwargs
        assert call_kwargs["name"] == "marketing-plan-agent"
        assert "instructions" in call_kwargs
        assert len(call_kwargs["tools"]) == 1

    def test_create_marketing_plan_agent_with_settings(self, monkeypatch):
        """model_settings が正しく渡されること"""
        from unittest.mock import MagicMock

        import src.agent_client as ac
        import src.agents.marketing_plan as mp

        mock_client = MagicMock()
        mock_client.as_agent.return_value = MagicMock()
        mock_client.get_web_search_tool.return_value = MagicMock()

        monkeypatch.setattr(ac, "_clients", {})
        monkeypatch.setattr(
            "src.agent_client.FoundryChatClient",
            lambda **kwargs: mock_client,
        )
        monkeypatch.setattr(
            "src.agent_client.DefaultAzureCredential",
            MagicMock,
        )

        mp.create_marketing_plan_agent(model_settings={"temperature": 0.3})
        call_kwargs = mock_client.as_agent.call_args.kwargs
        opts = call_kwargs["default_options"]
        assert opts["temperature"] == 0.3
        assert opts["max_output_tokens"] == 16384

    def test_instructions_contains_required_sections(self):
        """INSTRUCTIONS に必要な構成要素が含まれること"""
        from src.agents.marketing_plan import INSTRUCTIONS

        assert "キャッチコピー" in INSTRUCTIONS
        assert "ターゲット" in INSTRUCTIONS
        assert "KPI" in INSTRUCTIONS
        assert "景品表示法" in INSTRUCTIONS
