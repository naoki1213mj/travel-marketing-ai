"""品質評価 API と評価器ヘルパーのテスト"""

import pytest
from fastapi.testclient import TestClient

from src.api import evaluate as evaluate_module
from src.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_evaluate_rate_limiter(monkeypatch) -> None:
    """評価 API の rate limit 状態をテスト間で分離する。"""
    evaluate_module.limiter.reset()
    monkeypatch.delenv("ENABLE_CONTINUOUS_MONITORING", raising=False)
    monkeypatch.delenv("CONTINUOUS_MONITORING_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("ENABLE_EVALUATION_LOGGING", raising=False)
    monkeypatch.delenv("EVALUATION_LOGGING_ENABLED", raising=False)
    monkeypatch.delenv("EVALUATION_LOG_RETENTION_DAYS", raising=False)


def test_evaluate_travel_law_compliance_scores_all_required_items():
    response = """
    観光庁長官登録旅行業第123号
    取引条件と旅行条件を記載
    取消料・キャンセルポリシーあり
    3日目までの日程を掲載
    料金は 120,000円（税込）
    """

    result = evaluate_module._evaluate_travel_law_compliance(response, "")

    assert result["score"] == 1.0
    assert all(result["details"].values())


def test_evaluate_brochure_accessibility_scores_all_checks():
    html = """
        <html lang="ja"><body>
            <h1>京都の秋をゆったり楽しむ旅</h1>
            <p>期間限定の特典付きツアーを予約受付中。価格は 98,000円（税込）から。</p>
            <section>
                <p>予約方法: お問い合わせ窓口で受付中。電話で空席確認も可能です。</p>
            </section>
            <footer>観光庁長官登録旅行業第123号 / お問い合わせはこちら</footer>
    </body></html>
    """

    result = evaluate_module._evaluate_brochure_accessibility(html)

    assert result["score"] == 1.0
    assert all(result["details"].values())


def test_evaluate_cta_visibility_accepts_static_brochure_reservation_copy():
    html = """
        <html lang="ja"><body>
            <h1>京都の秋をゆったり楽しむ旅</h1>
            <p>今すぐ秋の京都旅をチェック。</p>
            <p>予約方法: お問い合わせ窓口で受付中。電話 03-1234-5678</p>
        </body></html>
    """

    result = evaluate_module._evaluate_cta_visibility(html)

    assert result["score"] == 1.0
    assert result["details"]["予約方法の明記"] is True
    assert "リンクまたはボタン" not in result["details"]


def test_evaluate_value_visibility_matches_actual_brochure_content():
    html = """
        <html lang="ja"><body>
            <h1>京都の秋をゆったり楽しむ旅</h1>
            <p>2泊3日 98,000円（税込）</p>
            <p>含まれるもの: 宿泊、朝食、現地サポート</p>
            <p>紅葉名所と街歩きを楽しめる人気プランです。</p>
        </body></html>
    """

    result = evaluate_module._evaluate_value_visibility(html)

    assert result["score"] == 1.0
    assert result["details"]["含まれるサービス"] is True
    assert result["details"]["訴求ポイント"] is True


def test_evaluate_plan_structure_detects_core_sections():
    response = """
    # 春の沖縄プラン
    キャッチコピー: 海と家族の笑顔をつなぐ旅
    ターゲット: ファミリー層
    プラン概要: 3日間の日程とルートを案内
    差別化ポイント: 添乗員付きで安心
    KPI: 予約数 200 件
    販促チャネル: SNS と広告
    価格帯: 89,000円
    """

    result = evaluate_module._evaluate_plan_structure(response)

    assert result["score"] == 1.0
    assert all(result["details"].values())


def test_evaluate_target_fit_readiness_handles_general_audience_briefs():
    result = evaluate_module._evaluate_target_fit_readiness(
        "春の沖縄で家族旅行プランを企画したい",
        """
        # 春の沖縄ファミリープラン
        ターゲット: 小学生の子どもがいるファミリー層
        プラン概要: 2泊3日で移動負担を抑えつつ体験を楽しめる行程
        価格帯: 89,000円（税込）
        含まれるもの: 宿泊、朝食、現地サポート
        問い合わせ: 専用窓口で予約変更も案内
        """,
    )

    assert result["score"] == 1.0
    assert result["details"]["依頼ターゲットとの整合"] is True


def test_evaluate_endpoint_does_not_log_to_foundry_by_default(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.2, "reason": "solid"},
            "fluency": {"score": 4.1, "reason": "clear"},
            "task_adherence": {"score": 3.8, "reason": "ok"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.3,
            "differentiation": 3.9,
            "kpi_validity": 4.0,
            "brand_tone": 4.1,
            "reason": "solid",
        }

    async def fake_log(record: dict[str, object]) -> str | None:
        calls.append(record)
        return "https://example.test/foundry"

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件\nターゲット: ファミリー",
            "html": "<html><body>予約はこちら 89,000円（税込） 期間限定 特典 サポート</body></html>",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["builtin"]["relevance"]["score"] == 4.0
    assert payload["marketing_quality"]["overall"] == 4.0
    assert payload["plan_quality"]["metrics"]["relevance"]["score"] == 4.0
    assert payload["asset_quality"]["metrics"]["cta_visibility"]["score"] > 0
    assert payload["custom"]["target_fit_readiness"]["score"] >= 0
    assert "senior_fit_readiness" not in payload["custom"]
    assert payload["custom"]["conversion_potential"]["score"] > 0
    assert payload["legacy_overall"] > 0
    assert payload["regression_guard"]["has_regressions"] is False
    assert "foundry_portal_url" not in payload
    assert calls == []


def test_evaluate_rejects_untrusted_owner_claims_when_auth_required(monkeypatch):
    """owner 認証必須時は会話へ保存する評価で未検証 bearer claims を拒否する。"""
    monkeypatch.setattr("src.config._get_azd_env_values", lambda: {})
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("REQUIRE_AUTHENTICATED_OWNER", "true")
    monkeypatch.delenv("TRUST_AUTH_HEADER_CLAIMS", raising=False)
    monkeypatch.delenv("TRUSTED_AUTH_HEADER_NAME", raising=False)

    response = client.post(
        "/api/evaluate",
        headers={"Authorization": "Bearer untrusted.token.value"},
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン",
            "conversation_id": "conv-eval",
            "artifact_version": 1,
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_HEADER_UNTRUSTED"


def test_evaluate_endpoint_logs_sanitized_foundry_payload_when_opted_in(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.2, "reason": "solid"},
            "fluency": {"score": 4.1, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.3,
            "differentiation": 3.9,
            "kpi_validity": 4.0,
            "brand_tone": 4.1,
            "reason": "solid",
        }

    async def fake_log(record: dict[str, object]) -> str | None:
        calls.append(record)
        return "https://example.test/foundry"

    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENABLE_EVALUATION_LOGGING", "true")
    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    raw_query = "Authorization: Bearer raw-secret Work IQ meeting note: confidential"
    raw_response = "# 春の沖縄プラン\nKPI: 予約数 200 件\ntranscript: raw customer call"
    raw_html = "<section data-token='secret'>予約はこちら 89,000円（税込）</section>"
    response = client.post(
        "/api/evaluate",
        json={
            "query": raw_query,
            "response": raw_response,
            "html": raw_html,
            "evidence": [
                {
                    "id": "ev-token",
                    "title": "需要データ",
                    "source": "fabric",
                    "quote": "Authorization: Bearer secret-token",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert len(calls) == 1
    record = calls[0]
    serialized = str(record)
    assert raw_query not in serialized
    assert raw_response not in serialized
    assert raw_html not in serialized
    assert "raw-secret" not in serialized
    assert "secret-token" not in serialized
    assert "raw customer call" not in serialized
    assert record["retention_days"] == 30
    assert record["redaction"]["raw_prompt_logged"] is False
    assert record["redaction"]["brochure_html_logged"] is False
    assert record["content_shape"] == {
        "query_chars": len(raw_query),
        "response_chars": len(raw_response),
    }
    assert record["plan_overall"] > 0
    assert record["evidence_count"] == 1


def test_evaluate_endpoint_schedules_continuous_monitoring_when_opted_in(monkeypatch):
    """継続監視は opt-in 時だけ sanitized payload で非同期登録する。"""
    scheduled: list[dict[str, object]] = []

    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.0, "reason": "solid"},
            "fluency": {"score": 4.0, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.0,
            "differentiation": 4.0,
            "kpi_validity": 4.0,
            "brand_tone": 4.0,
            "reason": "solid",
        }

    async def fake_log(record: dict[str, object]) -> str | None:
        return str(record.get("record_type") or "evaluation")

    def fake_schedule(_background_tasks, *, record: dict[str, object], sample_key: str, **_kwargs: object) -> bool:
        scheduled.append({"record": record, "sample_key": sample_key})
        return True

    monkeypatch.setenv("AZURE_AI_PROJECT_ENDPOINT", "https://example.services.ai.azure.com/api/projects/demo")
    monkeypatch.setenv("ENABLE_EVALUATION_LOGGING", "true")
    monkeypatch.setenv("ENABLE_CONTINUOUS_MONITORING", "true")
    monkeypatch.setenv("CONTINUOUS_MONITORING_SAMPLE_RATE", "1")
    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)
    monkeypatch.setattr(evaluate_module, "schedule_continuous_monitoring", fake_schedule)

    raw_query = "Authorization: Bearer raw-secret Work IQ confidential note"
    raw_response = "# 春の沖縄プラン\ntranscript: raw customer call"
    raw_html = "<html><body data-token='secret'>予約はこちら</body></html>"
    response = client.post(
        "/api/evaluate",
        json={
            "query": raw_query,
            "response": raw_response,
            "html": raw_html,
            "conversation_id": "conv-monitoring",
            "artifact_version": 1,
        },
    )

    assert response.status_code == 200
    assert len(scheduled) == 1
    record = scheduled[0]["record"]
    serialized = str(record)
    assert record["record_type"] == "evaluation_completion"
    assert raw_query not in serialized
    assert raw_response not in serialized
    assert raw_html not in serialized
    assert "raw-secret" not in serialized
    assert "raw customer call" not in serialized
    assert record["redaction"]["raw_work_iq_logged"] is False
    assert record["content_shape"]["html_chars"] == len(raw_html)


def test_evaluate_endpoint_accepts_sanitized_evidence_and_charts(monkeypatch):
    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.0, "reason": "solid"},
            "fluency": {"score": 4.0, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.0,
            "differentiation": 4.0,
            "kpi_validity": 4.0,
            "brand_tone": 4.0,
            "reason": "solid",
        }

    async def fake_log(_query: str, _response: str, _scores: dict) -> str | None:
        return None

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件\n根拠: 前年比とレビュー平均\nターゲット: ファミリー",
            "html": "<html lang='ja'><body><h1>予約はこちら</h1><p>98,000円（税込） 安心サポート</p><footer>登録番号</footer></body></html>",
            "evidence": [
                {
                    "id": "",
                    "title": "需要データ",
                    "source": "fabric",
                    "url": "javascript:alert(1)",
                    "quote": "Authorization: Bearer secret-token",
                    "metadata": {"region": "okinawa", "token": "secret"},
                },
                {"id": "ev-web", "title": "市場トレンド", "source": "web", "url": "https://example.com/report"},
            ],
            "charts": [
                {
                    "chart_type": "bar",
                    "title": "<p>unsafe</p>",
                    "series": ["sales"],
                    "data": [{"month": "4月", "sales": 100, "token": "secret"}],
                }
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"][0]["id"] == "eval-ev-1"
    assert "url" not in payload["evidence"][0]
    assert payload["evidence"][0]["quote"] == "[redacted]"
    assert payload["evidence"][0]["metadata"] == {"region": "okinawa"}
    assert payload["charts"][0]["title"] == "[redacted html]"
    assert payload["charts"][0]["data"] == [{"month": "4月", "sales": 100}]
    assert payload["evidence_quality"]["overall"] > 0
    assert any(finding["status"] in {"pass", "warn"} for finding in payload["findings"])
    assert any(finding["evidence_ids"] for finding in payload["findings"])


def test_evaluate_endpoint_restores_evidence_context_from_conversation(monkeypatch):
    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.0, "reason": "solid"},
            "fluency": {"score": 4.0, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.0,
            "differentiation": 4.0,
            "kpi_validity": 4.0,
            "brand_tone": 4.0,
            "reason": "solid",
        }

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ) -> dict:
        return {
            "messages": [
                {
                    "event": "tool_event",
                    "data": {
                        "tool": "search_sales_history",
                        "status": "completed",
                        "evidence": [{"id": "ev-v1", "title": "v1 sales", "source": "fabric"}],
                        "charts": [{"chart_type": "bar", "title": "v1 chart", "data": [{"sales": 100}]}],
                    },
                },
                {"event": "done", "data": {"metrics": {"latency_seconds": 1, "tool_calls": 1, "total_tokens": 1}}},
                {
                    "event": "tool_event",
                    "data": {
                        "tool": "web_search",
                        "status": "completed",
                        "evidence": [{"id": "ev-v2", "title": "v2 trend", "source": "web"}],
                    },
                },
                {"event": "done", "data": {"metrics": {"latency_seconds": 1, "tool_calls": 1, "total_tokens": 1}}},
            ],
            "metadata": {},
            "status": "completed",
            "input": "春の沖縄プランを作成",
        }

    async def fake_persist(
        conversation_id: str,
        artifact_version: int,
        result: dict,
        owner_id: str | None = None,
    ) -> dict | None:
        return {"version": 2, "round": 1, "created_at": "2026-04-02T01:00:00+00:00"}

    async def fake_log(_query: str, _response: str, _scores: dict) -> str | None:
        return None

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(evaluate_module, "_persist_evaluation_result", fake_persist)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件\n根拠: 前年比\nターゲット: ファミリー",
            "html": "<html><body>予約はこちら 98,000円（税込） 安心サポート 登録番号</body></html>",
            "conversation_id": "conv-1",
            "artifact_version": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence"] == [{"id": "ev-v2", "title": "v2 trend", "source": "web"}]
    assert payload["findings"][0]["evidence_ids"] == ["ev-v2"]
    assert payload["evaluation_meta"]["version"] == 2


def test_evaluate_endpoint_persists_grouped_result_for_version(monkeypatch):
    persist_calls: list[tuple[str, int, dict]] = []

    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.1, "reason": "solid"},
            "fluency": {"score": 4.2, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.2,
            "differentiation": 3.8,
            "kpi_validity": 4.0,
            "brand_tone": 4.1,
            "reason": "solid",
        }

    async def fake_persist(
        conversation_id: str,
        artifact_version: int,
        result: dict,
        owner_id: str | None = None,
    ) -> dict | None:
        persist_calls.append((conversation_id, artifact_version, result))
        return {"version": artifact_version, "round": 2, "created_at": "2026-04-02T01:00:00+00:00"}

    async def fake_log(_query: str, _response: str, _scores: dict) -> str | None:
        return None

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "_persist_evaluation_result", fake_persist)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件\nターゲット: ファミリー",
            "html": "<html><body>予約はこちら</body></html>",
            "conversation_id": "conv-1",
            "artifact_version": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["evaluation_meta"] == {
        "version": 2,
        "round": 2,
        "created_at": "2026-04-02T01:00:00+00:00",
    }
    assert len(persist_calls) == 1
    assert persist_calls[0][0] == "conv-1"
    assert persist_calls[0][1] == 2
    persisted = persist_calls[0][2]
    assert "plan_quality" in persisted
    assert "asset_quality" in persisted
    assert "regression_guard" in persisted
    assert "target_fit_readiness" in persisted["custom"]
    assert persisted["custom"]["conversion_potential"]["score"] >= 0


@pytest.mark.asyncio
async def test_persist_evaluation_result_ignores_malformed_saved_version(monkeypatch):
    """保存済み評価イベントの version が不正でも評価保存を継続する。"""
    persisted: list[dict] = []

    async def fake_get_conversation(conversation_id: str, owner_id: str | None = None):
        return {
            "id": conversation_id,
            "user_id": owner_id or "anonymous",
            "input": "初回",
            "status": "completed",
            "metadata": {},
            "messages": [
                {"event": "done", "data": {}},
                {"event": "evaluation_result", "data": {"version": "draft", "round": 1, "result": {}}},
            ],
        }

    async def fake_append_conversation_events(**kwargs):
        persisted.append(kwargs)
        return None

    monkeypatch.setattr(evaluate_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(evaluate_module, "append_conversation_events", fake_append_conversation_events)

    meta = await evaluate_module._persist_evaluation_result(
        "conv-eval-malformed",
        1,
        {"plan_quality": {"overall": 4.0}},
        owner_id="user-a",
    )

    assert meta is not None
    assert meta["version"] == 1
    assert meta["round"] == 1
    assert len(persisted) == 1


def test_evaluate_endpoint_detects_regression_against_previous_version(monkeypatch):
    async def fake_builtin(_query: str, _response: str) -> dict:
        return {
            "relevance": {"score": 4.0, "reason": "good"},
            "coherence": {"score": 4.0, "reason": "solid"},
            "fluency": {"score": 4.0, "reason": "clear"},
        }

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {
            "overall": 4.0,
            "appeal": 4.0,
            "differentiation": 4.0,
            "kpi_validity": 4.0,
            "brand_tone": 4.0,
            "reason": "steady",
        }

    previous_result = {
        "plan_quality": {
            "overall": 5.0,
            "summary": "stable",
            "focus_areas": [],
            "metrics": {
                "relevance": {"score": 5.0, "label": "依頼適合性"},
                "coherence": {"score": 5.0, "label": "構成の一貫性"},
            },
        },
        "asset_quality": {
            "overall": 5.0,
            "summary": "stable",
            "focus_areas": [],
            "metrics": {
                "cta_visibility": {"score": 5.0, "label": "予約導線の明確さ"},
            },
        },
    }

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ) -> dict:
        return {
            "messages": [
                {
                    "event": "evaluation_result",
                    "data": {
                        "version": 1,
                        "round": 1,
                        "result": previous_result,
                    },
                }
            ]
        }

    async def fake_persist(
        conversation_id: str,
        artifact_version: int,
        result: dict,
        owner_id: str | None = None,
    ) -> dict | None:
        return None

    async def fake_log(_query: str, _response: str, _scores: dict) -> str | None:
        return None

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", fake_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", fake_marketing)
    monkeypatch.setattr(evaluate_module, "get_conversation", fake_get_conversation)
    monkeypatch.setattr(evaluate_module, "_persist_evaluation_result", fake_persist)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "春の沖縄プランを作成",
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件\nターゲット: ファミリー",
            "html": "<html><body>予約はこちら</body></html>",
            "conversation_id": "conv-1",
            "artifact_version": 2,
        },
    )

    assert response.status_code == 200
    guard = response.json()["regression_guard"]
    assert guard["has_regressions"] is True
    assert any(item["key"] == "relevance" and item["area"] == "plan" for item in guard["degraded_metrics"])
    assert any(item["key"] == "cta_visibility" and item["area"] == "asset" for item in guard["degraded_metrics"])
    assert guard["plan_overall_delta"] < 0
    assert guard["asset_overall_delta"] < 0


def test_truncate_for_evaluation_keeps_short_text_intact():
    text = "短いテキスト"

    assert evaluate_module._truncate_for_evaluation(text, 20) == text


def test_truncate_for_evaluation_marks_trimmed_text():
    text = "a" * 50

    truncated = evaluate_module._truncate_for_evaluation(text, 10)

    assert truncated.startswith("a" * 10)
    assert truncated.endswith("[truncated]")


def test_evaluate_endpoint_returns_200_when_v2_evaluators_raise(monkeypatch):
    persist_calls: list[tuple[str, int]] = []

    async def broken_builtin(_query: str, _response: str) -> dict:
        raise RuntimeError("builtin evaluator unavailable")

    async def broken_marketing(_query: str, _response: str) -> dict:
        raise Exception("marketing evaluator unavailable")

    async def fake_persist(
        conversation_id: str,
        artifact_version: int,
        result: dict,
        owner_id: str | None = None,
    ) -> dict | None:
        persist_calls.append((conversation_id, artifact_version))
        return {"version": 2, "round": 1, "created_at": "2026-04-06T00:00:00+00:00"}

    async def fake_log(_query: str, _response: str, _scores: dict) -> str | None:
        return None

    async def fake_get_conversation(
        _conversation_id: str,
        owner_id: str | None = None,
        allow_cross_owner: bool = False,
    ) -> dict:
        return {
            "messages": [
                {
                    "event": "done",
                    "data": {
                        "conversation_id": "conv-v2",
                        "metrics": {"latency_seconds": 1, "tool_calls": 1, "total_tokens": 10},
                    },
                },
                {
                    "event": "done",
                    "data": {
                        "conversation_id": "conv-v2",
                        "metrics": {"latency_seconds": 1, "tool_calls": 1, "total_tokens": 10},
                    },
                },
            ]
        }

    monkeypatch.setattr(evaluate_module, "_run_builtin_evaluators", broken_builtin)
    monkeypatch.setattr(evaluate_module, "_run_marketing_quality_evaluator", broken_marketing)
    monkeypatch.setattr(evaluate_module, "_persist_evaluation_result", fake_persist)
    monkeypatch.setattr(evaluate_module, "_log_to_foundry", fake_log)
    monkeypatch.setattr(evaluate_module, "get_conversation", fake_get_conversation)

    response = client.post(
        "/api/evaluate",
        json={
            "query": "改善版の京都プランを評価",
            "response": "# 改善版企画書\nターゲット: シニア\nKPI: 予約数 120 件",
            "html": "<html><body>予約はこちら</body></html>",
            "conversation_id": "conv-v2",
            "artifact_version": 2,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["builtin"] == {"error": "builtin evaluator unavailable"}
    assert payload["marketing_quality"] == {"score": -1, "reason": "marketing evaluator unavailable"}
    assert payload["evaluation_meta"] is None or payload["evaluation_meta"]["version"] == 2
    assert persist_calls == [("conv-v2", 2)] or persist_calls == []
