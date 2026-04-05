"""mcp_server.improvement_brief のテスト。"""

import json

from mcp_server.improvement_brief import generate_improvement_brief_result


def test_generate_improvement_brief_result_extracts_priority_and_must_keep() -> None:
    """改善ブリーフ生成は優先課題と維持要素を返す"""
    result = generate_improvement_brief_result(
        plan_markdown=(
            "# 春の沖縄ファミリー旅\n\n"
            "## キャッチコピー\n家族で楽しむ海辺の休日\n\n"
            "## ターゲット\n春休みのファミリー層\n"
        ),
        evaluation_payload=json.dumps(
            {
                "builtin": {"relevance": {"score": 2, "reason": "便益が弱い"}},
                "custom": {
                    "travel_law_compliance": {
                        "score": 0.4,
                        "details": {"fee_display": False, "disclaimer": True},
                    }
                },
            },
            ensure_ascii=False,
        ),
        regulation_summary="⚠ 最安値表現に注意",
        rejection_history=json.dumps(["ターゲット像は維持したい"], ensure_ascii=False),
        user_feedback="",
    )

    assert result["priority_issues"]
    assert any(issue["label"] == "依頼適合性" for issue in result["priority_issues"])
    assert any(issue["label"] == "旅行業法準備度" for issue in result["priority_issues"])
    assert any("タイトル" in item for item in result["must_keep"])
    assert "維持すべき要素" in result["improvement_brief"]
