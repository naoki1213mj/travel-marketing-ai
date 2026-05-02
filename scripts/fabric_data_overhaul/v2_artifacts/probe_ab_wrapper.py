"""A/B probe comparing the old bloated wrapper vs new slim wrapper against live DA.

Validates the fix in `src/agents/data_search.py:_build_data_agent_question_v2`:
- OLD form: 6-line preamble + question (~587 chars total for 38-char question)
- NEW form: question only (~38 chars)

Both run against `Travel_Ontology_DA_v2` (same workspace, same DA, back-to-back).
If OLD returns polite refusal and NEW returns rich grounded answer, the fix is correct.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.agents.data_search import (  # noqa: E402
    _is_low_confidence_data_agent_answer,
    _has_grounded_metrics,
)

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
    f"/dataagents/{DA_V2_ID}/aiassistant/openai"
)

USER_QUESTION = "夏のハワイ学生旅行向けの売上・予約数・旅行者数・平均評価を教えてください"

OLD_WRAPPER_FORM = "\n".join(
    [
        "あなたは旅行マーケティング担当者向けの Fabric Data Agent (Travel_Ontology_DA_v2) です。",
        "lh_travel_marketing_v2 lakehouse の booking / customer / review / payment / cancellation / hotel / flight / campaign / inquiry / itinerary 10 テーブルから実データに基づくマーケ分析を返してください。",
        "回答は 1) 結論、2) 適用条件 (季節 / destination_region / customer_segment / age_band)、3) 主要指標 (売上 SUM(total_revenue_jpy) / 予約件数 COUNT / 旅行者数 SUM(pax_count) / 平均評価 AVG(rating))、4) 表またはランキング、5) 補足の順でまとめてください。",
        "実データの数値だけを使い、X/XX、○○、架空の例、プレースホルダーは絶対に使わないでください。",
        "内部の GQL、GraphQL、JSON、SQL、ツール呼び出しトレースは出力せず、マーケ担当者向けの自然な日本語で書いてください。",
        f"質問: {USER_QUESTION}",
    ]
)

NEW_SLIM_FORM = USER_QUESTION


def get_token() -> str:
    r = subprocess.run(
        [
            "az", "account", "get-access-token",
            "--resource", "https://analysis.windows.net/powerbi/api",
            "--query", "accessToken", "-o", "tsv",
        ],
        capture_output=True,
        text=True,
        shell=True,
        check=True,
    )
    return r.stdout.strip()


def run_probe(label: str, question_form: str) -> dict:
    """Run one probe via OpenAI assistants flow."""
    from openai import OpenAI
    import uuid as _uuid

    token = get_token()
    activity_id = str(_uuid.uuid4())
    client = OpenAI(
        base_url=BASE_URL,
        api_key="dummy",
        default_headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "ActivityId": activity_id,
            "x-ms-workload-resource-moniker": activity_id,
            "x-ms-ai-assistant-scenario": "aiskill",
            "x-ms-ai-aiskill-stage": "production",
        },
        default_query={"api-version": "2024-05-01-preview"},
        timeout=240,
    )

    started = time.time()
    assistant = client.beta.assistants.create(model="not used")
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=question_form)
    run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)
    deadline = started + 240
    while True:
        if time.time() > deadline:
            return {"label": label, "status": "TIMEOUT"}
        run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        if run.status in {"completed", "failed", "cancelled", "expired"}:
            break
        time.sleep(2)
    msgs = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=20)
    answer = ""
    for msg in msgs.data:
        if msg.role == "assistant":
            for chunk in msg.content:
                if hasattr(chunk, "text") and hasattr(chunk.text, "value"):
                    answer = chunk.text.value
                    break
            if answer:
                break
    return {
        "label": label,
        "status": run.status,
        "input_chars": len(question_form),
        "answer_chars": len(answer),
        "low_confidence": _is_low_confidence_data_agent_answer(answer),
        "has_grounded_metrics": _has_grounded_metrics(answer),
        "elapsed_s": round(time.time() - started, 1),
        "answer_first_400": answer[:400],
        "answer_full": answer,
    }


def main():
    print(f"USER_QUESTION ({len(USER_QUESTION)} chars):", USER_QUESTION)
    print()
    print(f"--- OLD WRAPPER ({len(OLD_WRAPPER_FORM)} chars) ---")
    old = run_probe("OLD", OLD_WRAPPER_FORM)
    print(json.dumps({k: v for k, v in old.items() if k != "answer_full"}, ensure_ascii=False, indent=2))
    print()
    print(f"--- NEW SLIM ({len(NEW_SLIM_FORM)} chars) ---")
    new = run_probe("NEW", NEW_SLIM_FORM)
    print(json.dumps({k: v for k, v in new.items() if k != "answer_full"}, ensure_ascii=False, indent=2))

    out_path = Path(__file__).parent / "probe_ab_results.json"
    out_path.write_text(json.dumps({"old": old, "new": new}, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"Saved to {out_path}")

    print()
    print("=== VERDICT ===")
    if old.get("low_confidence", True) and not new.get("low_confidence", True):
        print("PASS: OLD bloats → low_conf, NEW slim → high_conf. Fix is validated.")
        sys.exit(0)
    elif new.get("low_confidence"):
        print(f"FAIL: NEW slim still low_conf. answer_chars={new.get('answer_chars')}")
        sys.exit(1)
    else:
        print(f"INCONCLUSIVE: OLD low_conf={old.get('low_confidence')} NEW low_conf={new.get('low_confidence')}")
        sys.exit(2)


if __name__ == "__main__":
    main()
