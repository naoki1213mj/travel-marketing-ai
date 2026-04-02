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
    <html><body>
      期間限定の特典付きツアーを予約受付中。価格は 98,000円（税込）から。
      お問い合わせは電話またはURLから。キャンセルサポートあり。
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


def test_evaluate_endpoint_logs_to_foundry_in_background(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    async def fake_builtin(_query: str, _response: str) -> dict:
        return {"relevance": {"score": 4.0, "reason": "good"}}

    async def fake_marketing(_query: str, _response: str) -> dict:
        return {"overall": 4.0, "reason": "solid"}

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
            "response": "# 春の沖縄プラン\nKPI: 予約数 200 件",
            "html": "<html><body>予約はこちら 89,000円（税込） 期間限定 特典 サポート</body></html>",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["builtin"]["relevance"]["score"] == 4.0
    assert payload["marketing_quality"]["overall"] == 4.0
    assert "foundry_portal_url" not in payload
    assert len(calls) == 1
    assert calls[0][0] == "春の沖縄プランを作成"
