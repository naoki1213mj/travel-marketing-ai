"""品質評価 API と評価器ヘルパーのテスト"""

from fastapi.testclient import TestClient

from src.api import evaluate as evaluate_module
from src.main import app

client = TestClient(app)


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
            <a href="https://example.com/reserve">今すぐ予約</a>
            <footer>観光庁長官登録旅行業第123号 / お問い合わせはこちら</footer>
    </body></html>
    """

    result = evaluate_module._evaluate_brochure_accessibility(html)

    assert result["score"] == 1.0
    assert all(result["details"].values())


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


def test_evaluate_endpoint_logs_to_foundry_in_background(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

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

    async def fake_log(query: str, response: str, scores: dict) -> str | None:
        calls.append((query, response, scores))
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
    assert len(calls) == 1
    assert calls[0][0] == "春の沖縄プランを作成"


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

    async def fake_persist(conversation_id: str, artifact_version: int, result: dict) -> dict | None:
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

    async def fake_get_conversation(_conversation_id: str) -> dict:
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

    async def fake_persist(_conversation_id: str, _artifact_version: int, _result: dict) -> dict | None:
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
