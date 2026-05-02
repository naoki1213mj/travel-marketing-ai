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
        assert "ws-3iq-demo Lakehouse" in parsed["answer"]
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

        assert parsed["source"] == "Fabric SQL"
        assert "ws-3iq-demo Lakehouse の SQL endpoint から実データを集計しました" in parsed["answer"]
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
        assert "Category は旅行カテゴリ/顧客カテゴリ/旅行タイプ" in prompt
        assert "review-only の質問では travel_review を使い" in prompt
        assert "沖縄、ハワイ、春、夏、ファミリー、学生などが明記されているのに全エリア" in prompt
        assert "どの条件が0件か" in prompt
        assert "条件緩和はユーザーに再指定を求めず自動で行ってください" in prompt
        assert "「旅行先A」「○○件」「例のフォーマットです」" in prompt
        assert "未知の指標を聞かれた場合も、提案だけで終わらず" in prompt
        assert "同じ旅行先を複数行に出してはいけません" in prompt
        assert "売上=SUM(Price)" in prompt
        assert "レビュー件数=COUNT(*)" in prompt
        assert "Transaction_ID で travel_sales と travel_review を結合" in prompt
        assert "「学生」は Age_group が 20代" in prompt
        assert "Age_group が 30代/40代" in prompt
        assert "SUM(Price)" in prompt
        assert "Emotions 分布" in prompt
        assert "GQL、GraphQL、JSON" in prompt
        assert "X/XX/XXX" in prompt

    def test_low_confidence_data_agent_answer_detection(self):
        """Data Agent の回答不能文は、数字を含んでいても低信頼として扱う。"""
        import src.agents.data_search as ds

        weak_answer = "30代ファミリー向けの売上トレンドは提示できませんでした。必要であれば追加提示してください。"
        technical_failure_answer = "Lakehouse上で該当クエリを実行しましたが、技術的な理由（内部エラー）により必要なデータを取得できませんでした。"
        placeholder_answer = "合計売上は ¥X,XXX,XXX、予約件数は XX件です。以下は分析例です。"
        missing_sales_answer = "売上上位・合計売上・予約件数の具体的数値はデータ不足のためデータなしです。レビュー件数は18件です。"
        missing_sales_variant = "売上上位プラン、合計売上金額、予約件数に該当するデータが存在しませんでした。"
        safe_unavailable_answer = "安全に算出できるデータなし。ご希望があれば条件を変更してください。"
        gql_leak_answer = '```gql\nquery { travel_sales { Travel_destination Price } }\n```'
        json_leak_answer = '{"query": "SELECT * FROM travel_sales", "status": "failed"}'
        ignored_filter_answer = (
            "使用条件\n- 旅行先・カテゴリ・年齢層の指定なし／全エリア・全年齢層・全カテゴリ対象\n"
            "売上 17,000,000 円、予約数 40 件です。"
        )
        partial_technical_answer = (
            "夏のハワイ旅行に関する学生層の売上・予約数・旅行者数は技術的な理由により集計できませんでした。"
            "レビュー件数は3件、平均評価は4～5です。"
        )
        placeholder_table_answer = (
            "表：旅行先別ランキング（例）\n"
            "| 旅行先 | 売上 | 予約数 |\n"
            "| 旅行先A | ○○○○○○ | ○○件 |\n"
            "※上記は例のフォーマットです。"
        )
        concrete_answer = "沖縄 2泊3日が売上上位。合計売上 1,022,000 円、予約 8 件。"

        assert ds._is_low_confidence_data_agent_answer(weak_answer) is True
        assert ds._is_low_confidence_data_agent_answer(technical_failure_answer) is True
        assert ds._is_low_confidence_data_agent_answer(placeholder_answer) is True
        assert ds._is_low_confidence_data_agent_answer(missing_sales_answer) is True
        assert ds._is_low_confidence_data_agent_answer(missing_sales_variant) is True
        assert ds._is_low_confidence_data_agent_answer(safe_unavailable_answer) is True
        assert ds._is_low_confidence_data_agent_answer(gql_leak_answer) is True
        assert ds._is_low_confidence_data_agent_answer(json_leak_answer) is True
        assert ds._is_low_confidence_data_agent_answer(ignored_filter_answer) is True
        assert ds._is_low_confidence_data_agent_answer(partial_technical_answer) is True
        assert ds._is_low_confidence_data_agent_answer(placeholder_table_answer) is True
        assert ds._is_low_confidence_data_agent_answer(concrete_answer) is False

    def test_select_data_agent_answer_prefers_high_confidence_final_message(self):
        """assistant が複数メッセージを出したときに最終メッセージが成功なら採用する。

        Data Agent は self-retry のとき「技術的なエラーが発生したので分解します」のような
        中間ステータスメッセージを emit する。全結合すると最終回答が具体数値を含んでいても
        強い失敗フレーズで低信頼扱いされるので、最終メッセージが高信頼なら単独で返す。
        """
        import src.agents.data_search as ds

        interim = "技術的なエラーにより一部取得できませんでしたので、質問を分解して再集計します。少々お待ちください。"
        final = "結論: 沖縄全体の合計売上は 13,664,000 円、予約件数は 68 件、旅行者数は 203 人でした。"

        result = ds._select_data_agent_answer([interim, final])

        assert result == final
        assert "技術的なエラー" not in result

    def test_select_data_agent_answer_falls_back_to_concat_when_final_low_confidence(self):
        """最終メッセージが低信頼なら全メッセージ結合した文字列を返す。"""
        import src.agents.data_search as ds

        interim = "結論: 沖縄全体の合計売上は 5,000,000 円でした。"
        final_low = "技術的な都合で詳細データ取得ができませんでした。"

        result = ds._select_data_agent_answer([interim, final_low])

        assert interim in result
        assert final_low in result

    def test_select_data_agent_answer_handles_empty_list(self):
        """assistant メッセージが空のときは空文字列を返す。"""
        import src.agents.data_search as ds

        assert ds._select_data_agent_answer([]) == ""

    def test_select_data_agent_answer_single_message(self):
        """単一メッセージはそのまま返す。"""
        import src.agents.data_search as ds

        single = "結論: 合計売上 58,166,000 円、予約 79 件、旅行者数 235 人。"
        assert ds._select_data_agent_answer([single]) == single

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

    def test_resolve_data_agent_version_defaults_to_v1(self, monkeypatch):
        """v1 がデフォルト。env 未設定なら本番 (旧 schema) と同じ挙動を維持する。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(ds, "get_settings", lambda: {})
        assert ds._resolve_data_agent_version() == "v1"

    def test_resolve_data_agent_version_v2_requires_url(self, monkeypatch):
        """version=v2 でも URL_V2 が空なら v1 にフォールバックする (誤設定への安全網)。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(
            ds,
            "get_settings",
            lambda: {"fabric_data_agent_runtime_version": "v2", "fabric_data_agent_url_v2": ""},
        )
        assert ds._resolve_data_agent_version() == "v1"

    def test_resolve_data_agent_version_v2_when_url_set(self, monkeypatch):
        """version=v2 + URL_V2 が両方そろっているときだけ v2 を使う。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(
            ds,
            "get_settings",
            lambda: {
                "fabric_data_agent_runtime_version": "v2",
                "fabric_data_agent_url_v2": "https://api.fabric.microsoft.com/v1/workspaces/x/dataagents/y/aiassistant/openai",
            },
        )
        assert ds._resolve_data_agent_version() == "v2"

    def test_resolve_data_agent_url_returns_version_specific_url(self, monkeypatch):
        """version 指定に応じて v1 / v2 の URL を出し分ける。v1 URL は v2 経路で参照されない。"""
        import src.agents.data_search as ds

        monkeypatch.setattr(
            ds,
            "get_settings",
            lambda: {
                "fabric_data_agent_url": "https://v1-url",
                "fabric_data_agent_url_v2": "https://v2-url",
            },
        )
        assert ds._resolve_data_agent_url("v1") == "https://v1-url"
        assert ds._resolve_data_agent_url("v2") == "https://v2-url"

    def test_build_data_agent_question_v2_returns_question_as_is(self):
        """v2 用プロンプトは aiInstructions v6 (Phase 9.6) が値マッピング・SQL テンプレート・
        品質ガードを内蔵しているため、アプリ側で preamble を重ねず質問のみを渡す。

        2026-05-02 の live App Insights ログ (587-char 質問 → 219-char polite refusal) と
        standalone probe (raw 38-char → rich grounded answer) の比較で、preamble が
        NL2Ontology を confuse させることが確認されている。
        """
        import src.agents.data_search as ds

        question = "春の沖縄ファミリー施策を分析して"
        prompt = ds._build_data_agent_question_v2(question)

        # 質問がそのまま返される (preamble なし)
        assert prompt == question

    def test_build_data_agent_question_v2_no_branding_or_schema_preamble(self):
        """v2 wrapper は preamble を持たない invariant を保証する。

        将来 wrapper を「ちょっとしたコンテキスト」で再膨張させるリグレッションを防ぐため、
        v2 用プロンプトに以下が含まれていないことを assert する:
          - v2 ブランディング ("Travel_Ontology_DA_v2", "lh_travel_marketing_v2")
          - スキーマ列挙 ("booking / customer / review ...")
          - 回答フォーマット指示 ("結論", "適用条件", "主要指標")
          - placeholder ガード ("プレースホルダー", "実データの数値")
        これらはすべて DA 側の aiInstructions v6 が担当する。
        """
        import src.agents.data_search as ds

        prompt = ds._build_data_agent_question_v2("春の沖縄ファミリー施策を分析して")

        forbidden_substrings = [
            "Travel_Ontology_DA_v2",
            "lh_travel_marketing_v2",
            "booking / customer",
            "結論",
            "適用条件",
            "主要指標",
            "プレースホルダー",
            "実データの数値",
            "GraphQL",
            "GQL",
        ]
        for forbidden in forbidden_substrings:
            assert forbidden not in prompt, (
                f"v2 wrapper は preamble を持たないはずだが {forbidden!r} が含まれた。"
                f"DA 側 aiInstructions v6 と重複してプロンプト膨張 → NL2Ontology confuse の"
                f"リグレッションリスク。"
            )

    def test_build_data_agent_question_v2_length_ceiling(self):
        """v2 wrapper は質問を verbatim で返すので、length は質問とほぼ一致する。

        ある日「ちょっとした文脈を 1 行だけ追加」したくなっても、prompt 長が質問 + 64
        char を超えないように歯止めをかける。Live ログで 587-char prompt → polite
        refusal が確認されているので、200 char 以下のごく短い質問では明確な余裕を取る。
        """
        import src.agents.data_search as ds

        for question in [
            "夏のハワイ学生旅行向けプランを企画して",
            "夏のハワイ学生旅行向けの売上・予約数・旅行者数・平均評価を教えてください",
            "春の沖縄",
        ]:
            prompt = ds._build_data_agent_question_v2(question)
            assert len(prompt) <= len(question) + 64, (
                f"v2 wrapper が質問 ({len(question)} chars) より 64 char 以上膨張した。"
                f"prompt={prompt!r}"
            )

    def test_build_data_agent_question_v1_keeps_full_preamble(self):
        """v1 (Travel_Ontology_DA, travel_sales / travel_review) は引き続きアプリ側
        preamble を使う。v1 の aiInstructions は v2 ほど richly populated でないため。
        """
        import src.agents.data_search as ds

        prompt = ds._build_data_agent_question("春の沖縄ファミリー施策を分析して")

        # v1 用 schema 名・指示が残っていることを確認 (regression防止)
        assert "travel_sales" in prompt or "travel_review" in prompt
        assert "春の沖縄ファミリー施策を分析して" in prompt
        # v1 wrapper は依然として長い (slim 化はしない)
        assert len(prompt) > 200

    def test_data_agent_answer_with_sales_metrics_is_not_low_confidence(self):
        """一部項目がデータなしでも売上実数があれば Data Agent 成功として扱う。"""
        import src.agents.data_search as ds

        answer = (
            "売上サマリ表\n"
            "| 旅行先 | 日程 | 売上合計 | 予約件数 | 合計人数 |\n"
            "| 沖縄 | 2泊3日 | 1,022,000円 | 6件 | 17人 |\n"
            "平均評価・レビュー傾向: 安全に算出できるデータなし"
        )

        assert ds._is_low_confidence_data_agent_answer(answer) is False

    def test_low_confidence_detected_for_technical_error_with_descriptive_numbers(self):
        """「技術的なエラーが発生」「具体的な分析結果は取得できません」の文面は、
        ターゲット説明用の数値（「20代」「2人以上のグループ」など）が混在していても低信頼として扱う。

        実環境では Data Agent の最終回答がこの形を取り、説明文中の「2人」が
        `\\d[\\d,]*(?:\\s*)人` に偶然マッチして has_specific_metric=True となり、
        SQL フォールバック経路を通らず 0.85 信頼度のカードが表示されていた。
        """
        import src.agents.data_search as ds

        regression_answer = (
            "分析の途中で技術的なエラーが発生し、夏のハワイ学生旅行向けの販売・レビュー詳細データ取得ができませんでした。"
            "【現状の説明とご提案】"
            "- 今回はデータ取得プロセスでエラーが発生したため、詳細な数値やランキング、人気ポイント・不満点などの"
            "具体的な分析結果は取得できませんでした。"
            "- しかしながら、「学生旅行」ターゲットの抽出や分析可能な切り口としては、年齢（20代中心）、"
            "Number_of_people（2人以上のグループ）、夏季（6月〜8月）が候補となります。"
        )

        assert ds._is_low_confidence_data_agent_answer(regression_answer) is True

    def test_low_confidence_detected_for_technical_circumstances_variant(self):
        """ライブ環境 (2026-04-30 02:18 UTC, conv 392799b7) で観測された、
        既存の "技術的なエラー" / "技術的な制約" / "技術的な理由" を回避する新しい言い回し:

        - 「技術的な都合により」
        - 「データ抽出ができませんでした」
        - 「システム的なエラー（内部処理…）」

        これらは Data Agent が "申し訳ありません… 取得しようとしましたが…" の謝罪付きで
        失敗を表明する文面で、説明用の使用条件（「ハワイ」「夏」「20代」など）に
        含まれる数値で has_specific_metric=True となり、0.85 信頼の "Fabric Data Agent 回答"
        カードがそのまま表示されていた。STRONG パターンに追加して低信頼判定する。
        """
        import src.agents.data_search as ds

        live_failure_quote = (
            "結論 申し訳ありませんが、現時点で「夏のハワイ・学生向け」の売上・予約数・旅行者数・"
            "平均評価・レビュー分析を取得しようとしましたが、技術的な都合によりデータ抽出が"
            "できませんでした。 使用条件 - 旅行先：ハワイ限定 - 期間：夏（6月、7月、8月） "
            "- セグメント：学生（Age_groupが20代または学生を示唆する条件） - 分析種別："
            "売上+レビュー  主要指標・表 今回は上記の厳密条件で、システム的なエラー（内部処理）"
            "が発生し、抽出できませんでした。"
        )

        assert ds._is_low_confidence_data_agent_answer(live_failure_quote) is True

    @pytest.mark.asyncio
    async def test_query_data_agent_replaces_technical_circumstances_card_with_sql_supplement(
        self, monkeypatch
    ):
        """ライブ環境で観測された「技術的な都合により」失敗を含む Data Agent 回答が、
        0.85 信頼の "Fabric Data Agent 回答" カードではなく
        "Fabric Lakehouse 集計" カード (relevance=0.9) に置き換わることを検証する。"""
        import src.agents.data_search as ds

        live_failure_quote = (
            "結論 申し訳ありませんが、現時点で「夏のハワイ・学生向け」の売上・予約数・旅行者数・"
            "平均評価・レビュー分析を取得しようとしましたが、技術的な都合によりデータ抽出が"
            "できませんでした。 使用条件 - 旅行先：ハワイ限定 - 期間：夏（6月、7月、8月） "
            "- セグメント：学生（Age_groupが20代または学生を示唆する条件） - 分析種別："
            "売上+レビュー  主要指標・表 今回は上記の厳密条件で、システム的なエラー（内部処理）"
            "が発生し、抽出できませんでした。"
        )

        async def fake_data_agent(question: str) -> str:
            return live_failure_quote

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "ハワイ 4泊5日",
                    "destination": "ハワイ",
                    "season": "summer",
                    "revenue": 5892000,
                    "pax": 12,
                    "customer_segment": "20代",
                    "booking_count": 4,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "ハワイ", "rating": 5, "comment": "ビーチが最高でした"}],
        )

        events: list = []
        with tool_event_context(events.append, agent_name="data-search-agent", step=1):
            result = await ds.query_data_agent(
                "夏のハワイ学生向けに、売上・予約数・旅行者数・平均評価・代表レビューから施策の示唆を出してください。"
            )

        parsed = json.loads(result)
        assert parsed["source"] == "Fabric SQL"
        assert "ハワイ 4泊5日" in parsed["answer"]

        evidence_titles: list[str] = []
        evidence_relevances: list[float] = []
        for event in events:
            for ev in event.get("evidence", []) or []:
                evidence_titles.append(ev.get("title", ""))
                relevance = ev.get("relevance")
                if isinstance(relevance, (int, float)):
                    evidence_relevances.append(float(relevance))
        assert "Fabric Data Agent 回答" not in evidence_titles
        assert "Fabric Lakehouse 集計" in evidence_titles
        assert 0.85 not in evidence_relevances

    @pytest.mark.asyncio
    async def test_query_data_agent_does_not_emit_high_confidence_card_for_technical_error(
        self, monkeypatch
    ):
        """技術的エラーの最終回答が Fabric Data Agent 回答カード (relevance=0.85) として
        表示されず、Fabric Lakehouse 集計カードに置き換わることを検証する。"""
        import src.agents.data_search as ds

        regression_answer = (
            "分析の途中で技術的なエラーが発生し、夏のハワイ学生旅行向けの販売・レビュー詳細データ取得ができませんでした。"
            "【現状の説明とご提案】"
            "- 今回はデータ取得プロセスでエラーが発生したため、詳細な数値やランキング、人気ポイント・不満点などの"
            "具体的な分析結果は取得できませんでした。"
            "- しかしながら、「学生旅行」ターゲットの抽出や分析可能な切り口としては、年齢（20代中心）、"
            "Number_of_people（2人以上のグループ）、夏季（6月〜8月）が候補となります。"
        )

        async def fake_data_agent(question: str) -> str:
            return regression_answer

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "ハワイ 4泊5日",
                    "destination": "ハワイ",
                    "season": "summer",
                    "revenue": 5892000,
                    "pax": 12,
                    "customer_segment": "20代",
                    "booking_count": 4,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "ハワイ", "rating": 5, "comment": "ビーチが最高でした"}],
        )

        events: list = []
        with tool_event_context(events.append, agent_name="data-search-agent", step=1):
            result = await ds.query_data_agent(
                "夏のハワイ学生向けに、売上・予約数・旅行者数・平均評価・代表レビューから施策の示唆を出してください。"
            )

        parsed = json.loads(result)
        assert parsed["source"] == "Fabric SQL"
        assert "ハワイ 4泊5日" in parsed["answer"]
        assert "5,892,000 円" in parsed["answer"]

        # Evidence の citation card を検証: 0.85 のカードは出さず、補強カード (0.9) に置換する。
        evidence_titles: list[str] = []
        evidence_relevances: list[float] = []
        for event in events:
            for ev in event.get("evidence", []) or []:
                evidence_titles.append(ev.get("title", ""))
                relevance = ev.get("relevance")
                if isinstance(relevance, (int, float)):
                    evidence_relevances.append(float(relevance))
        assert "Fabric Data Agent 回答" not in evidence_titles
        assert "Fabric Lakehouse 集計" in evidence_titles
        assert 0.85 not in evidence_relevances

    def test_low_confidence_detected_for_nl2ontology_internal_error(self):
        """ライブ環境 (2026-04-30 05:33 UTC, conv f94774cc) で観測された
        NL2Ontology / InternalError の英語インフラエラー文面を低信頼として扱う。

        以前のリリースでは英語の Data Agent インフラ層エラー
        ("Failed to generate query", "NL2Ontology", '"code":"InternalError"')
        が STRONG パターンに含まれておらず、Fabric Data Agent 回答カードとして
        relevance=0.85 で UI に出てしまっていた。Fabric Lakehouse 集計カードに
        置き換わるよう、これらは低信頼判定する必要がある。
        """
        import src.agents.data_search as ds

        nl2ontology_failure = (
            "Failed to generate query. The error was: Failed to generate "
            'NL2Ontology query with error "{"code":"InternalError",'
            '"subCode":0,"message":"An internal error has occurred."}"'
        )

        # Wrap 後に観測された文面 (prefix + body) が低信頼判定されることも検証する。
        wrapped_failure = (
            "Fabric Data Agent の最終回答が十分な実数を含まなかったため、"
            "Data Agent の実行結果を根拠として返します。\n"
            f"{nl2ontology_failure}"
        )

        assert ds._is_low_confidence_data_agent_answer(nl2ontology_failure) is True
        assert ds._is_low_confidence_data_agent_answer(wrapped_failure) is True

    def test_low_confidence_detected_for_internal_workings_soft_apology(self):
        """2026-05-01 condition matrix で観測された
        「内部の仕組み上エラー」系のソフト謝罪文面を低信頼として扱う。

        例 (春のパリ):
            "「春のパリの売上」について、システムで集計を試みましたが、
             旅行先別・月別の条件で集計するときに内部の仕組み上エラーが発生しました。"

        これは取得不能を曖昧に伝える文面で、具体的な売上指標を含まない。
        Fabric Lakehouse 集計カードに置き換わるよう低信頼判定する必要がある。
        """
        import src.agents.data_search as ds

        soft_apology = (
            "「春のパリの売上」について、システムで集計を試みましたが、"
            "旅行先別・月別の条件で集計するときに内部の仕組み上エラーが発生しました。\n"
            "現時点では「パリ」の春（3月・4月・5月）について売上サマリー指標を"
            "直接取得できませんでした。"
        )
        assert ds._is_low_confidence_data_agent_answer(soft_apology) is True

    def test_low_confidence_detected_for_ga_particle_failure(self):
        """Data Agent の「実データの取得**が**できませんでした」型のソフト謝罪を、
        が-particle 抜きの「取得できません」パターンが拾えなかった live regression
        (2026-05-01 14:19 UTC, conv 67b363bb) に対する固定。

        ハワイ学生旅行プロンプトで再現:
            "現在、ハワイ学生旅行（夏季）に関する実データの取得ができませんでした。
             そのため、対象条件や値のマッピング、分析軸の文言などを見直して再度ご質問ください。"

        この文面は SQL 経由で本物の販売データが取れているのに 85% 信頼で UI に
        さらされていたため、SQL fallback に必ず置き換わるよう低信頼判定されること
        を保証する。
        """
        import src.agents.data_search as ds

        live_failure = (
            "現在、ハワイ学生旅行（夏季）に関する実データの取得ができませんでした。"
            "そのため、対象条件や値のマッピング、分析軸の文言などを見直して再度ご質問ください。"
            "補足：「学生旅行」「夏」「ハワイ」に該当する正規化された条件"
            "（例：destination_region='ハワイ'、season='summer'、customer_segment='学生'など）"
            "や具体的な年次指定を追加いただくことで照合精度が上がる場合があります。"
        )
        assert ds._is_low_confidence_data_agent_answer(live_failure) is True

    def test_concrete_answer_with_real_data_phrase_stays_high_confidence(self):
        """「実データ」というフレーズを含む成功回答が、低信頼判定で誤って
        SQL fallback に置き換えられないことを保証する (rubber-duck 監査)。

        過広なパターン（例: 「実データの取得」だけ）を入れると、成功回答も
        誤検出してしまう。が-particle ありの取得失敗パターンだけを追加した
        ことで、成功回答は正しく素通りすることを確認する。
        """
        import src.agents.data_search as ds

        success_with_real_data_phrase = (
            "## 結論\n"
            "ハワイ向け学生旅行の実データを取得した結果、夏季の予約は 1,243 件、\n"
            "売上は ¥845,200,000 でした。\n"
            "## 売上ランキング\n"
            "| プラン | 売上 | 件数 |\n"
            "| --- | --- | --- |\n"
            "| ハワイ cruise | ¥350,406,823 | 482 件 |\n"
        )
        assert ds._is_low_confidence_data_agent_answer(success_with_real_data_phrase) is False

    def test_normalized_filters_extracts_segment_and_season(self):
        """日本語の segment / season 表記から canonical 英語値を 1 ヒットだけ抽出する。"""
        import src.agents.data_search as ds

        assert ds._extract_normalized_filters("夏のハワイ学生旅行向けプランを企画して") == {
            "customer_segment": "student",
            "season": "summer",
        }
        assert ds._extract_normalized_filters("春の沖縄ファミリープラン") == {
            "customer_segment": "family",
            "season": "spring",
        }
        assert ds._extract_normalized_filters("カップル向けの京都旅行") == {
            "customer_segment": "couple",
        }
        assert ds._extract_normalized_filters("シニア向けの団体旅行") is None, (
            "segment が 2 ヒット (senior + group) のときは曖昧として None"
        )
        assert ds._extract_normalized_filters("春と夏の比較") is None, (
            "season が 2 ヒット (spring + summer) のときは曖昧として None"
        )
        assert ds._extract_normalized_filters("ハワイの売上を教えて") is None, (
            "segment / season どちらもヒットしないときは None"
        )

    def test_normalized_filters_skips_ambiguous_japanese(self):
        """rubber-duck 監査で指摘された曖昧語 (若い旅行者 / ビジネス) は誤マッピング
        リスクが高いので map に入っていないこと (= None になること)。"""
        import src.agents.data_search as ds

        assert ds._extract_normalized_filters("若い旅行者向けのプラン") is None
        assert ds._extract_normalized_filters("ビジネス利用のホテル") is None

    def test_structured_retry_question_includes_normalized_filters(self):
        """structured retry プロンプトが英語小文字 canonical 値を箇条書きで含むこと。"""
        import src.agents.data_search as ds

        retry = ds._build_structured_retry_question(
            "夏のハワイ学生旅行向けプラン",
            {"customer_segment": "student", "season": "summer"},
        )
        assert "正規化済みフィルタ条件" in retry
        assert "- customer_segment: student" in retry
        assert "- season: summer" in retry
        assert "緩和" in retry, "緩和禁止の指示を含む"
        assert "元の質問: 夏のハワイ学生旅行向けプラン" in retry
        # 元の質問の日本語キーワードも保持される (NL2Ontology が destination='ハワイ' を抽出するため)
        assert "ハワイ" in retry

    @pytest.mark.asyncio
    async def test_query_data_agent_structured_retry_recovers_no_data(self, monkeypatch):
        """1 回目で「実データの取得ができませんでした」が返ったとき、structured retry で
        正規化済み英語値を渡して 2 回目を投げ、Data Agent から実データを取得することを確認する
        (Phase 10 P02 / P07 系の no_data 救済)。"""
        import src.agents.data_search as ds

        first_failure = (
            "現在、ハワイ学生旅行（夏季）に関する実データの取得ができませんでした。"
            "再度ご質問ください。"
        )
        retry_success = (
            "## 結論\n"
            "夏季ハワイの学生 (customer_segment=student) 向け予約は 207 件、\n"
            "売上は ¥45,820,000 でした。\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| --- | --- |\n"
            "| 予約件数 | 207 件 |\n"
            "| 平均単価 | ¥221,400 |\n"
            "| 平均評価 | 4.3 / 5 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            if "正規化済みフィルタ条件" in question:
                return retry_success
            return first_failure

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        result = await ds.query_data_agent("夏のハワイ学生旅行向けプランを企画して")

        assert len(call_history) == 2, "1 回目失敗 → 2 回目 structured retry の合計 2 回呼び出される"
        assert "正規化済みフィルタ条件" in call_history[1]
        assert "customer_segment: student" in call_history[1]
        assert "season: summer" in call_history[1]
        # 2 回目で実データが取れたら Fabric Data Agent 由来の応答として返される (SQL fallback ではない)
        import json as _json
        payload = _json.loads(result)
        assert payload["source"] == "Fabric Data Agent", (
            f"structured retry 成功時は Fabric Data Agent 由来として表示される "
            f"(SQL fallback ではない); got source={payload['source']}"
        )
        assert payload.get("attempt") == "structured_retry"
        assert "207 件" in payload["answer"]

    @pytest.mark.asyncio
    async def test_query_data_agent_no_retry_when_filters_unextractable(self, monkeypatch):
        """日本語 segment / season が抽出できない曖昧クエリでは structured retry を
        スキップし、SQL fallback に直行することを確認する (回帰防止)。"""
        import src.agents.data_search as ds

        always_fail = (
            "現在、データの取得ができませんでした。"
            "もう少し条件を絞ってください。"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return always_fail

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        # _build_fabric_sql_analysis はテスト環境では実 lakehouse がないので空
        # (CSV / hardcoded fallback は _build_fabric_sql_analysis 外なので OK)

        await ds.query_data_agent("ハワイの売上を教えて")

        assert len(call_history) == 1, (
            "曖昧クエリ (segment / season ヒットなし) では structured retry をスキップする"
        )

    @pytest.mark.asyncio
    async def test_query_data_agent_no_retry_on_v1_runtime(self, monkeypatch):
        """structured retry prompt は v2 lakehouse スキーマ (customer_segment / season を
        英語小文字で直書き) に依存しているため、v1 runtime ではスキップしなければならない。
        v1 (travel_sales: Category / Age_group) で v2 prompt を流すと NL2Ontology が
        さらに混乱して品質が悪化するリスクがあるため、rubber-duck #1 で gate を追加した。"""
        import src.agents.data_search as ds

        always_fail = "現在、実データの取得ができませんでした。"

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return always_fail

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        # v1 runtime を強制
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v1")

        # 日本語 segment / season が両方ヒットするクエリでも v1 では retry しない
        await ds.query_data_agent("夏のハワイ学生旅行向けプランを企画して")

        assert len(call_history) == 1, (
            "v1 runtime では structured retry を絶対にスキップ "
            "(retry prompt が v2 schema 専用のため)"
        )

    @pytest.mark.asyncio
    async def test_query_data_agent_high_confidence_first_attempt_does_not_retry(self, monkeypatch):
        """1 回目で高信頼の実データが返ってきた場合、structured retry は
        トリガされず attempt='first' のまま返ること (Phase 10 grade-A 回帰防止)。"""
        import json as _json

        import src.agents.data_search as ds

        first_success = (
            "## 結論\n"
            "夏のハワイ学生旅行 (customer_segment=student) は 207 件、売上は ¥45,820,000 です。\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| --- | --- |\n"
            "| 予約件数 | 207 件 |\n"
            "| 平均評価 | 4.3 / 5 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return first_success

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        result = await ds.query_data_agent("夏のハワイ学生旅行向けプランを企画して")

        assert len(call_history) == 1, (
            "1 回目で高信頼が取れたら追加 retry は走らない (Phase 10 grade-A 回帰防止)"
        )
        payload = _json.loads(result)
        assert payload.get("attempt") == "first"
        assert payload["source"] == "Fabric Data Agent"

    @pytest.mark.asyncio
    async def test_structured_retry_uses_original_user_prompt_when_set(self, monkeypatch):
        """rubber-duck `agent1-da-prompt-preserve` BLOCKING #2:
        Agent1 LLM が tool 引数の `question` でユーザの explicit filters
        (夏 / ハワイ / 学生) を drop しても、`original_user_prompt_context` で
        元プロンプトが set されていれば、structured retry は元プロンプトから
        filters を抽出して正しい canonical 値 (customer_segment=student,
        season=summer) で retry することを確認する。"""
        import src.agents.data_search as ds

        first_failure = (
            "ご質問の内容では具体的なデータ抽出ができませんでした。"
            "条件を絞ってください。"
        )
        retry_success = (
            "## 結論\n"
            "夏季ハワイの学生 207 件、売上 ¥45,820,000\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| 予約件数 | 207 件 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            if "正規化済みフィルタ条件" in question:
                return retry_success
            return first_failure

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        # ContextVar に元のユーザプロンプトを set した状態で、tool 引数には
        # LLM が rewrite した「家族構成」を含む曖昧な prompt を渡す
        rewritten_arg = (
            "ユーザー指示は不明瞭ですが、旅行プラン企画のための基礎分析として、"
            "売上履歴と顧客レビューから主要なターゲット (年代・家族構成・旅行動機)、"
            "季節別・地域別の売上トレンドを教えてください"
        )
        with ds.original_user_prompt_context("夏のハワイ学生旅行向けプランを企画して"):
            await ds.query_data_agent(rewritten_arg)

        assert len(call_history) == 2, (
            "ContextVar に元プロンプトがあれば、低信頼検出後に structured retry が走る"
        )
        retry_prompt = call_history[1]
        # 元プロンプトから抽出された canonical 値が retry に含まれる
        assert "customer_segment: student" in retry_prompt, (
            "元プロンプト (学生) から student が抽出されているはず "
            f"(rewritten arg の家族構成が混入していたら family になる); got: {retry_prompt[:300]}"
        )
        assert "season: summer" in retry_prompt
        # 「家族構成 → family」が誤抽出されていないこと
        assert "customer_segment: family" not in retry_prompt, (
            "rewritten arg の「家族構成」を拾って family を誤抽出していないか確認"
        )

    @pytest.mark.asyncio
    async def test_structured_retry_falls_back_to_question_when_no_context(self, monkeypatch):
        """ContextVar が set されていない場合 (CLI smoke / standalone test) は
        後方互換で tool 引数 `question` をそのまま structured retry の filter source
        として使うことを確認する。"""
        import src.agents.data_search as ds

        first_failure = "実データの取得ができませんでした。"
        retry_success = (
            "## 結論\n"
            "夏季ハワイ学生 207 件 / 売上 ¥45,820,000\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            if "正規化済みフィルタ条件" in question:
                return retry_success
            return first_failure

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        # ContextVar 未 set。tool 引数自体に filters が含まれているケース
        await ds.query_data_agent("夏のハワイ学生旅行向けプランを教えて")

        assert len(call_history) == 2
        assert "customer_segment: student" in call_history[1]
        assert "season: summer" in call_history[1]

    def test_get_original_user_prompt_default_empty(self):
        """ContextVar 未 set のときは空文字列を返す (test isolation 担保)。"""
        import src.agents.data_search as ds

        assert ds._get_original_user_prompt() == ""

    def test_original_user_prompt_context_resets_after_with_block(self):
        """`original_user_prompt_context` が with block を抜けたら ContextVar が
        前の値 (空) に戻ることを確認する (test pollution / cross-conversation leak 防止)。"""
        import src.agents.data_search as ds

        assert ds._get_original_user_prompt() == ""
        with ds.original_user_prompt_context("夏のハワイ学生旅行"):
            assert ds._get_original_user_prompt() == "夏のハワイ学生旅行"
        # with 抜けた後は元に戻る
        assert ds._get_original_user_prompt() == ""

    @pytest.mark.asyncio
    async def test_first_call_reconciliation_when_rewritten_drops_user_filters(self, monkeypatch):
        """rubber-duck `prompt-preserve-impl-review` BLOCKING #1:
        ContextVar に元プロンプトが set されていて、tool 引数 `question` が
        ユーザの explicit filters を drop している場合、**1 回目の DA 呼び出し前に**
        元プロンプトを prepend して filter を復元する (low-confidence 待ちでは遅い)。
        DA が broad grounded answer を返すと低信頼判定にならず誤コホートが UI に
        出るリスクがあるため。"""
        import json as _json

        import src.agents.data_search as ds

        # DA が broad grounded answer (= 低信頼検出されない) を返すパス
        broad_grounded = (
            "## 結論\n"
            "全体の年代・家族構成別の傾向を分析しました。\n"
            "売上は ¥120,000,000、予約 850 件、平均評価 4.0 / 5 です。\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| 予約件数 | 850 件 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return broad_grounded

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        rewritten_arg = (
            "ユーザー指示は不明瞭ですが、年代・家族構成・旅行動機の基礎分析として、"
            "売上履歴と顧客レビューから主要なターゲット情報を教えてください"
        )
        with ds.original_user_prompt_context("夏のハワイ学生旅行向けプランを企画して"):
            result = await ds.query_data_agent(rewritten_arg)

        # 1 回目の prompt は reconciled 版でなければならない
        assert len(call_history) == 1, "DA が高信頼回答を返したので retry は走らない"
        first_prompt = call_history[0]
        assert "夏のハワイ学生旅行向けプランを企画して" in first_prompt, (
            "1 回目 DA call には元ユーザプロンプトが verbatim で含まれているべき "
            f"(BLOCKING #1 fix); got: {first_prompt[:300]}"
        )
        # rewritten arg も参考として含めておく (LLM の elaboration を捨てない)
        assert rewritten_arg in first_prompt or "参考:" in first_prompt
        # attempt label が反映される
        payload = _json.loads(result)
        assert payload.get("attempt") == "first_reconciled"

    @pytest.mark.asyncio
    async def test_first_call_no_reconciliation_when_no_filters_dropped(self, monkeypatch):
        """元プロンプトと rewritten の両方とも filter が抽出できないとき (汎用クエリ)、
        reconciliation は skip して既存挙動を維持する (回帰防止)。"""
        import json as _json

        import src.agents.data_search as ds

        broad_grounded = (
            "## 結論\n"
            "ハワイの売上は ¥80,000,000、予約 500 件です。\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| 予約件数 | 500 件 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return broad_grounded

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        # 元 = "ハワイの売上を教えて" は filter 抽出 None (segment / season なし)
        # rewritten = 同上
        with ds.original_user_prompt_context("ハワイの売上を教えて"):
            result = await ds.query_data_agent("ハワイの売上について教えてください")

        assert len(call_history) == 1
        # reconciliation は走らない
        first_prompt = call_history[0]
        assert "ユーザの実際の質問" not in first_prompt, (
            "filter 抽出できないクエリでは reconciliation を skip する"
        )
        payload = _json.loads(result)
        assert payload.get("attempt") == "first", (
            "filter 抽出されないので reconciliation 不要、attempt='first' のまま"
        )

    @pytest.mark.asyncio
    async def test_first_call_no_reconciliation_when_rewritten_added_filters(self, monkeypatch):
        """rewritten が元プロンプトより MORE filters を持つ (LLM が情報を追加した) とき、
        元プロンプト側に missing filter はないので reconciliation は skip する
        (rewritten の elaboration を上書きしない)。"""
        import json as _json

        import src.agents.data_search as ds

        broad_grounded = (
            "## 結論\n"
            "夏のハワイ学生旅行は予約 207 件、売上 ¥45,820,000 です。\n"
            "## 主要指標\n"
            "| 指標 | 値 |\n"
            "| 予約件数 | 207 件 |\n"
        )

        call_history: list[str] = []

        async def fake_data_agent(question: str) -> str:
            call_history.append(question)
            return broad_grounded

        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(ds, "_resolve_fabric_data_agent_runtime", lambda: "rest")
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")

        # 元 = "ハワイ" だけ (no filters), rewritten = LLM が "夏" "学生" を補完済み
        with ds.original_user_prompt_context("ハワイの分析"):
            result = await ds.query_data_agent("夏のハワイ学生旅行向けの売上を教えてください")

        assert len(call_history) == 1
        first_prompt = call_history[0]
        # 元プロンプトに filter がないので missing_filters は空 → reconciliation skip
        assert "ユーザの実際の質問" not in first_prompt
        payload = _json.loads(result)
        assert payload.get("attempt") == "first"

    @pytest.mark.asyncio
    async def test_context_var_propagates_across_asyncio_create_task(self):
        """rubber-duck `prompt-preserve-impl-review` BLOCKING #2:
        Agent Framework が `agent.run()` 内部で `asyncio.create_task` 経由で
        tool を呼ぶ場合でも、ContextVar が parent → child へ伝播することを確認する
        (asyncio の child task は default で parent context をコピーするはず)。"""
        import asyncio

        import src.agents.data_search as ds

        captured: list[str] = []

        async def child_task() -> None:
            captured.append(ds._get_original_user_prompt())

        with ds.original_user_prompt_context("夏のハワイ学生旅行"):
            # asyncio.create_task でも contextvar が伝播することを確認
            task = asyncio.create_task(child_task())
            await task
            # to_thread でも伝播する (Python 3.9+ の asyncio.to_thread は contextvar 維持)

            def sync_in_thread() -> str:
                return ds._get_original_user_prompt()

            thread_value = await asyncio.to_thread(sync_in_thread)
            captured.append(thread_value)

        assert captured == ["夏のハワイ学生旅行", "夏のハワイ学生旅行"], (
            "ContextVar は asyncio.create_task / asyncio.to_thread 経由でも "
            f"parent context をコピーするはず; captured={captured}"
        )

    @pytest.mark.asyncio
    async def test_query_data_agent_replaces_nl2ontology_error_with_sql_supplement(
        self, monkeypatch
    ):
        """ライブ環境で観測された NL2Ontology / InternalError 文面を含む Data Agent 回答が、
        0.85 信頼の Fabric Data Agent 回答カードではなく Fabric Lakehouse 集計カード (relevance=0.9)
        に置き換わることを検証する。"""
        import src.agents.data_search as ds

        nl2ontology_failure = (
            "Failed to generate query. The error was: Failed to generate "
            'NL2Ontology query with error "{"code":"InternalError",'
            '"subCode":0,"message":"An internal error has occurred."}"'
        )

        async def fake_data_agent(question: str) -> str:
            return nl2ontology_failure

        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)
        monkeypatch.setattr(
            ds,
            "_get_sales_data_from_fabric",
            lambda **_kwargs: [
                {
                    "plan_name": "ハワイ 4泊5日",
                    "destination": "ハワイ",
                    "season": "summer",
                    "revenue": 5892000,
                    "pax": 12,
                    "customer_segment": "20代",
                    "booking_count": 4,
                }
            ],
        )
        monkeypatch.setattr(
            ds,
            "_get_reviews_from_fabric",
            lambda **_kwargs: [{"plan_name": "ハワイ", "rating": 5, "comment": "ビーチが最高でした"}],
        )

        events: list = []
        with tool_event_context(events.append, agent_name="data-search-agent", step=1):
            result = await ds.query_data_agent(
                "夏のハワイ学生向けの売上、予約数、旅行者数を Fabric Data Agent で分析して。"
            )

        parsed = json.loads(result)
        assert parsed["source"] == "Fabric SQL"
        assert "ハワイ 4泊5日" in parsed["answer"]

        evidence_titles: list[str] = []
        evidence_relevances: list[float] = []
        evidence_quotes: list[str] = []
        for event in events:
            for ev in event.get("evidence", []) or []:
                evidence_titles.append(ev.get("title", ""))
                evidence_quotes.append(str(ev.get("quote", "")))
                relevance = ev.get("relevance")
                if isinstance(relevance, (int, float)):
                    evidence_relevances.append(float(relevance))
        assert "Fabric Data Agent 回答" not in evidence_titles
        assert "Fabric Lakehouse 集計" in evidence_titles
        assert 0.85 not in evidence_relevances
        # NL2Ontology の生エラー文面が evidence card に漏れていないことを確認する。
        assert not any("NL2Ontology" in quote for quote in evidence_quotes)
        assert not any("Failed to generate" in quote for quote in evidence_quotes)

    def test_has_yen_amount_helper(self):
        """¥ / ￥ / 円 各形式と桁不足ケースの判定を確認する。"""
        import src.agents.data_search as ds

        assert ds._has_yen_amount("¥38,926,615") is True
        assert ds._has_yen_amount("￥1,022,000") is True
        assert ds._has_yen_amount("38,926,615 円") is True
        assert ds._has_yen_amount("1234円") is True
        assert ds._has_yen_amount("売上は ¥850,000 でした") is True
        # 桁不足 / 0 / 接頭・接尾なしは除外
        assert ds._has_yen_amount("¥0") is False
        assert ds._has_yen_amount("¥123") is False
        assert ds._has_yen_amount("100円") is False
        assert ds._has_yen_amount("id 12345 was used") is False
        assert ds._has_yen_amount("予約は 39 件でした") is False

    def test_has_count_metric_helper(self):
        """件 / 名 / 人 を伴う数値表現を判定する。

        `泊` (例: 2泊3日) は期間表現で集計件数ではないため grounded override の count metric
        としては扱わない (rubber-duck `grounded-metrics-impl-review` 2026-05-02 BLOCKING fix)。
        """
        import src.agents.data_search as ds

        assert ds._has_count_metric("131名") is True
        assert ds._has_count_metric("39件") is True
        assert ds._has_count_metric("8 件") is True
        assert ds._has_count_metric("1,234人") is True
        # 期間 / その他は count として扱わない
        assert ds._has_count_metric("2泊3日") is False
        assert ds._has_count_metric("ボトル 1 本") is False
        assert ds._has_count_metric("¥38,926,615") is False
        assert ds._has_count_metric("名前は田中") is False

    def test_has_grounded_metrics_requires_both_yen_and_count(self):
        """grounded 判定は ¥ 金額と件/名/人 の **両方** が必要 (rubber-duck 監査)。"""
        import src.agents.data_search as ds

        assert ds._has_grounded_metrics("¥38,926,615 / 39件 / 131名") is True
        assert ds._has_grounded_metrics("売上 1,022,000 円、予約 8 件") is True
        # 片方だけだと grounded ではない
        assert ds._has_grounded_metrics("131名のみ") is False
        assert ds._has_grounded_metrics("¥38,926,615 のみ") is False
        # 桁不足 ¥ + 件 は grounded ではない (¥0 / ¥123 のような trivial case)
        assert ds._has_grounded_metrics("¥0、0件") is False

    def test_grounded_data_agent_answer_with_soft_disclaimers_stays_high_confidence(self):
        """Live agent_style probe (2026-05-02) で観測された 5000+ 字の grounded narrative が、
        埋め込まれた soft disclaimer (取得ができません / 抽出できません / ご希望があれば /
        集計できません) によって誤って低信頼判定されないことを保証する (rubber-duck
        `grounded-metrics-fix-review` 反映)。

        以前は `_LOW_CONFIDENCE_DATA_AGENT_PATTERNS` / `_STRONG_DATA_AGENT_FAILURE_PATTERNS`
        が grounded narrative 内の partial-data caveat を拾って 5/5 trial 全て低信頼判定
        していた。¥ + 件/名 の両方が揃っている grounded 回答は、disclaimer phrase が
        混在していても high-conf として扱う。
        """
        import src.agents.data_search as ds

        grounded_with_disclaimers = (
            "## 結論\n"
            "夏のハワイ学生旅行は、平均単価 ¥302,233／名で、131名が利用し、"
            "総売上は約 ¥38,926,615 です。\n\n"
            "## 売上明細\n"
            "| 売上 | 予約数 | 旅行者数 | 平均単価 |\n"
            "| --- | --- | --- | --- |\n"
            "| ¥38,926,615 | 39件 | 131名 | ¥302,233 |\n\n"
            "## 注意事項\n"
            "- 学生属性は直接判別不可なため参考値です\n"
            "- プランごとの詳細集計できませんでした\n"
            "- 一部 (売上トレンド・人気プラン・単価・リピート率) は該当データ抽出できません\n"
            "- 実データの取得ができませんでした (年次別の細目)\n"
            "- ご希望があれば追加分析を行います\n"
        )
        assert ds._is_low_confidence_data_agent_answer(grounded_with_disclaimers) is False

    def test_low_confidence_when_hard_infra_error_with_grounded_metrics(self):
        """grounded metric が含まれていても、HARD インフラエラー
        (技術的なエラー / システムエラー / 障害) は最優先で低信頼扱い。
        rubber-duck blocking #1 の対応: grounded override の前に HARD failures を
        チェックして、本物の error を grounded metric で誤って high-conf 化しない。"""
        import src.agents.data_search as ds

        infra_error_with_metrics = (
            "技術的なエラーが発生しましたが、参考までに過去の集計値を表示します。\n"
            "売上 ¥38,926,615、予約 39 件、131名"
        )
        assert ds._is_low_confidence_data_agent_answer(infra_error_with_metrics) is True

    def test_low_confidence_when_filters_ignored_with_grounded_metrics(self):
        """grounded metric が含まれていても、ユーザのフィルタを無視した広域回答は低信頼扱い。
        ユーザの聞きたかった分析ではないため SQL fallback に置き換わるべき。"""
        import src.agents.data_search as ds

        ignored_filter_with_metrics = (
            "使用条件\n"
            "- 旅行先・カテゴリ・年齢層の指定なし／全エリア・全年齢層・全カテゴリ対象\n\n"
            "売上 17,000,000 円、予約 40 件です。"
        )
        assert ds._is_low_confidence_data_agent_answer(ignored_filter_with_metrics) is True

    def test_low_confidence_when_yen_with_only_nights_and_disclaimer(self):
        """rubber-duck `grounded-metrics-impl-review` BLOCKING regression:
        `2泊3日` を count metric として grounded override に通してしまうと、
        `¥ + 2泊3日 + 集計できません` 型の弱い回答が誤って high-conf 化する。

        例: `おすすめは沖縄2泊3日、価格は¥50,000です。プランごとの詳細集計できませんでした。`
        は SQL fallback で補強されるべき (low_conf=True を期待)。
        """
        import src.agents.data_search as ds

        nights_only_with_disclaimer = (
            "おすすめは沖縄2泊3日、価格は¥50,000です。"
            "プランごとの詳細集計できませんでした。"
        )
        # 期間表現 (2泊3日) は count metric ではないので grounded override されない →
        # SOFT disclaimer "集計できません" が _STRONG_DATA_AGENT_FAILURE_PATTERNS で拾われ低信頼。
        assert ds._has_grounded_metrics(nights_only_with_disclaimer) is False
        assert ds._is_low_confidence_data_agent_answer(nights_only_with_disclaimer) is True

    @pytest.mark.asyncio
    async def test_query_data_agent_keeps_grounded_answer_with_disclaimers(self, monkeypatch):
        """grounded narrative + soft disclaimers の DA 回答が SQL fallback に置き換わらず、
        Fabric Data Agent 回答 (relevance=0.85) として UI に出ることを保証する。

        rubber-duck blocking #3 (integration test) に対応: helper-level test だけでなく
        `query_data_agent` の orchestration まで通して確認することで、low-conf misclassification
        が demo path に再発しないことを protect する。
        """
        import src.agents.data_search as ds

        grounded_with_disclaimers = (
            "## 結論\n"
            "夏のハワイ学生旅行は、平均単価 ¥302,233／名で、131名が利用し、"
            "総売上は約 ¥38,926,615 です。\n\n"
            "| 売上 | 予約数 | 旅行者数 |\n"
            "| --- | --- | --- |\n"
            "| ¥38,926,615 | 39件 | 131名 |\n\n"
            "## 注意事項\n"
            "- 学生属性は直接判別不可なため参考値です\n"
            "- プランごとの詳細集計できませんでした\n"
            "- ご希望があれば追加分析を行います\n"
        )

        async def fake_data_agent(question: str) -> str:
            call_log.append(question)
            return grounded_with_disclaimers

        call_log: list[str] = []
        monkeypatch.setattr(ds, "get_settings", lambda: {"fabric_data_agent_runtime": "rest"})
        # v2 retry-gate: grounded answer は structured retry に進まないこと (rubber-duck)
        monkeypatch.setattr(ds, "_resolve_data_agent_version", lambda: "v2")
        monkeypatch.setattr(ds, "_query_data_agent", fake_data_agent)

        # SQL fallback は呼ばれてはいけないので、呼ばれた場合は直ちに test を fail させる。
        def sql_should_not_be_called(**_kwargs):
            raise AssertionError(
                "Fabric SQL fallback should not be invoked when DA returns grounded answer with disclaimers"
            )

        monkeypatch.setattr(ds, "_get_sales_data_from_fabric", sql_should_not_be_called)
        monkeypatch.setattr(ds, "_get_reviews_from_fabric", sql_should_not_be_called)

        events: list = []
        with tool_event_context(events.append, agent_name="data-search-agent", step=1):
            result = await ds.query_data_agent(
                "夏のハワイ学生旅行向けプランを企画して"
            )

        parsed = json.loads(result)
        assert parsed["source"] == "Fabric Data Agent"
        assert "¥38,926,615" in parsed["answer"]
        assert parsed["attempt"] == "first"
        # v2 retry-gate: grounded with disclaimers は 1 回で確定し、structured retry を起動しないこと。
        assert len(call_log) == 1, f"Expected exactly 1 DA call, got {len(call_log)}: {call_log}"

        evidence_titles: list[str] = []
        evidence_relevances: list[float] = []
        for event in events:
            for ev in event.get("evidence", []) or []:
                evidence_titles.append(ev.get("title", ""))
                relevance = ev.get("relevance")
                if isinstance(relevance, (int, float)):
                    evidence_relevances.append(float(relevance))
        assert "Fabric Data Agent 回答" in evidence_titles
        assert "Fabric Lakehouse 集計" not in evidence_titles
        assert 0.85 in evidence_relevances


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
        monkeypatch.setattr(bg, "_recent_conversation_id", "banner-fallback")

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

    def test_image_settings_are_isolated_per_conversation(self):
        """並行する 2 つの conversation の image_settings がクロス汚染しないこと

        Regression test for the cross-user data leak fixed by replacing the
        single global ``_image_settings_fallback`` mutable with a
        conversation-keyed ``_conversation_image_settings`` dict.
        """
        import src.agents.brochure_gen as bg

        # Reset state
        bg._conversation_image_settings.clear()
        bg._recent_conversation_id = ""
        bg._image_settings_var.set({})

        # User A: select GPT Image 2 with conversation conv-A
        bg.set_current_conversation_id("conv-A")
        bg.set_current_image_settings({"image_model": "gpt-image-2"})

        # User B (different async context, simulated): select MAI with conv-B
        bg.set_current_conversation_id("conv-B")
        bg.set_current_image_settings({"image_model": "MAI-Image-2"})

        # B's set must NOT have overwritten A's stored settings
        assert bg._conversation_image_settings["conv-A"] == {"image_model": "gpt-image-2"}
        assert bg._conversation_image_settings["conv-B"] == {"image_model": "MAI-Image-2"}

        # Re-entering conv-A's context must read A's settings, not B's
        bg.set_current_conversation_id("conv-A")
        bg._image_settings_var.set({})  # simulate context lost
        assert bg._get_current_image_settings() == {"image_model": "gpt-image-2"}

        # Cleanup
        bg.clear_image_settings_for_conversation("conv-A")
        bg.clear_image_settings_for_conversation("conv-B")
        assert "conv-A" not in bg._conversation_image_settings
        assert "conv-B" not in bg._conversation_image_settings

    def test_set_current_image_settings_no_conversation_does_not_leak(self):
        """conversation_id 未設定なら global state を汚さないこと"""
        import src.agents.brochure_gen as bg

        bg._conversation_image_settings.clear()
        bg._recent_conversation_id = ""
        bg._conversation_id_var.set("")
        bg._image_settings_var.set({})

        bg.set_current_image_settings({"image_model": "gpt-image-2"})

        # ContextVar はセットされるが、conversation スコープ辞書には書かれない
        assert bg._image_settings_var.get() == {"image_model": "gpt-image-2"}
        assert bg._conversation_image_settings == {}

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
    async def test_generate_promo_video_legacy_avatar_style_is_constrained_to_lisa(self, monkeypatch):
        """legacy avatar_style 指定でも Lisa/casual-sitting に固定する"""
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
                return b'{"id": "promo-job-legacy"}'

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
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lisa"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual-sitting"
        assert "gesture.show-front-1" in payload["inputs"][0]["content"]
        assert "gesture.hello" not in payload["inputs"][0]["content"]

    @pytest.mark.asyncio
    async def test_generate_promo_video_respects_env_overrides_without_avatar_config(self, monkeypatch):
        """動画生成は voice / bitrate 等を反映しつつ avatar は Lisa に固定する"""
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
        monkeypatch.setenv("VIDEO_GEN_BACKGROUND_COLOR", "#11223344")
        monkeypatch.setenv("VIDEO_GEN_BITRATE_KBPS", "5500")
        monkeypatch.setattr(vg, "DefaultAzureCredential", lambda: _Credential())
        monkeypatch.setattr(vg.urllib.request, "urlopen", _fake_urlopen)

        result = await vg.generate_promo_video("沖縄の海と文化を体験できる旅です。")
        parsed = json.loads(result)
        payload = captured["payload"]

        assert parsed["status"] == "submitted"
        assert payload["avatarConfig"]["talkingAvatarCharacter"] == "lisa"
        assert payload["avatarConfig"]["talkingAvatarStyle"] == "casual-sitting"
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
