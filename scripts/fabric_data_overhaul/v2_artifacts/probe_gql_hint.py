"""Probe Fabric Data Agent with a TEMPORARY prompt-injection GQL hint to verify
that adding GQL anti-pattern guidance fixes the booking_id-leak server_error
before we modify the live data agent definition.

rubber-duck approved approach:
- Test the hint via user-message injection only (no live write)
- Compare WITH and WITHOUT hint on the same prompt
- Success criterion: nl2code stops emitting booking_id in aggregate queries
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from openai import OpenAI  # noqa: E402

from src.agents.data_search import _is_low_confidence_data_agent_answer  # noqa: E402

WS = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE = f"https://api.fabric.microsoft.com/v1/workspaces/{WS}/dataagents/{DA}/aiassistant/openai"

GQL_HINT_BLOCK = """
## CRITICAL GQL constraints (this query targets the analyze_ontology / NL2Ontology tool):
- ❌ aggregate を伴う RETURN に booking_id / customer_id / 表示列を絶対に含めない (server_error の原因)。
- ❌ destination_region / season / customer_segment に LOWER() を付けない。値マッピング表で正規化済みの文字列をそのまま使う。
- ✅ MATCH-WHERE-RETURN は 1 文字 alias (b/c/r/p) を使う。
- ✅ 集計のみ返す例 (単一条件サマリ):
  ```
  MATCH (b:booking)
  WHERE b.destination_region = "ハワイ"
    AND b.booking_status IN ["confirmed", "completed"]
  RETURN SUM(b.total_revenue_jpy) AS revenue_jpy,
         COUNT(b) AS bookings,
         SUM(b.pax) AS travelers
  ```
- ✅ booking + customer の join (segment 絞り込み, 例: 学生 = customer_segment='student'):
  ```
  MATCH (b:booking)-[:booking_has_customer]->(c:customer)
  WHERE b.destination_region = "ハワイ"
    AND b.season = "summer"
    AND c.customer_segment = "student"
    AND b.booking_status IN ["confirmed", "completed"]
  RETURN SUM(b.total_revenue_jpy) AS revenue_jpy,
         COUNT(b) AS bookings,
         SUM(b.pax) AS travelers
  ```
- ✅ 値マッピングは英語小文字: 学生→student / ファミリー→family / カップル→couple / シニア→senior / 出張→business / 春→spring / 夏→summer / 秋→autumn / 冬→winter
"""


def get_token() -> str:
    r = subprocess.run(
        [
            "az", "account", "get-access-token",
            "--resource", "https://analysis.windows.net/powerbi/api",
            "--query", "accessToken", "-o", "tsv",
        ],
        capture_output=True, text=True, shell=True, check=True,
    )
    return r.stdout.strip()


def make_client() -> OpenAI:
    aid = str(uuid.uuid4())
    return OpenAI(
        base_url=BASE,
        api_key="dummy",
        default_headers={
            "Authorization": f"Bearer {get_token()}",
            "Accept": "application/json",
            "ActivityId": aid,
            "x-ms-workload-resource-moniker": aid,
            "x-ms-ai-assistant-scenario": "aiskill",
            "x-ms-ai-aiskill-stage": "production",
        },
        default_query={"api-version": "2024-05-01-preview"},
        timeout=420,
    )


def probe(prompt: str, label: str) -> dict:
    print(f"\n{'='*70}\n=== {label} ===\n{'='*70}")
    print(f"prompt={prompt[:200]!r}")
    c = make_client()
    a = c.beta.assistants.create(model="not used")
    th = c.beta.threads.create()
    print(f"thread_id={th.id}")

    # Cancel any orphan runs on this (shared) thread
    try:
        for r in c.beta.threads.runs.list(thread_id=th.id).data:
            if r.status in {"queued", "in_progress", "requires_action"}:
                print(f"cancelling orphan {r.id} status={r.status}")
                try:
                    c.beta.threads.runs.cancel(thread_id=th.id, run_id=r.id)
                except Exception as e:
                    print(f"  cancel err: {str(e)[:80]}")
        time.sleep(15)
    except Exception as e:
        print(f"orphan check err: {str(e)[:80]}")

    # Fresh thread if cleanup killed it
    try:
        c.beta.threads.messages.list(thread_id=th.id, limit=1)
    except Exception:
        th = c.beta.threads.create()
        print(f"new thread_id={th.id}")

    try:
        c.beta.threads.messages.create(thread_id=th.id, role="user", content=prompt)
    except Exception as e:
        return {"label": label, "error": f"msg_create: {str(e)[:200]}"}

    run = c.beta.threads.runs.create(thread_id=th.id, assistant_id=a.id)
    deadline = time.time() + 300
    while time.time() < deadline:
        run = c.beta.threads.runs.retrieve(thread_id=th.id, run_id=run.id)
        if run.status in {"completed", "failed", "cancelled", "requires_action", "expired"}:
            break
        time.sleep(5)

    out = {"label": label, "status": run.status}
    if getattr(run, "last_error", None):
        out["last_error"] = str(run.last_error)

    # Pull steps to capture nl2code output
    try:
        steps = c.beta.threads.runs.steps.list(thread_id=th.id, run_id=run.id, order="asc")
        nl2code_outputs = []
        for st in steps.data:
            sd = getattr(st, "step_details", None)
            for tc in getattr(sd, "tool_calls", []) or []:
                fn = getattr(tc, "function", None)
                if fn and getattr(fn, "name", "") == "analyze.database.nl2code":
                    nl2code_outputs.append(getattr(fn, "output", "")[:1500])
        out["nl2code_outputs"] = nl2code_outputs
    except Exception as e:
        out["steps_err"] = str(e)[:120]

    # Assistant final answer
    try:
        msgs = c.beta.threads.messages.list(thread_id=th.id, order="desc")
        chunks = []
        for m in msgs.data:
            if m.role == "assistant":
                for ct in m.content or []:
                    if hasattr(ct, "text") and ct.text:
                        chunks.append(ct.text.value)
        final = "\n".join(chunks)
        out["final_answer"] = final[:2000]
        out["low_conf"] = _is_low_confidence_data_agent_answer(final) if final else None
    except Exception as e:
        out["msgs_err"] = str(e)[:120]

    return out


def main() -> None:
    user_q = "夏のハワイ学生旅行向けプランを企画して"

    # Baseline: no hint
    baseline_prompt = (
        "あなたは Fabric Data Agent (Travel_Ontology_DA_v2) です。"
        "lh_travel_marketing_v2 から実データだけを返してください。"
        f"\n質問: {user_q}"
    )
    baseline = probe(baseline_prompt, "baseline-no-hint")

    # With hint
    hinted_prompt = (
        "あなたは Fabric Data Agent (Travel_Ontology_DA_v2) です。"
        "lh_travel_marketing_v2 から実データだけを返してください。"
        + GQL_HINT_BLOCK
        + f"\n質問: {user_q}"
    )
    hinted = probe(hinted_prompt, "with-gql-hint")

    out_path = Path(__file__).parent / "probe_gql_hint_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"baseline": baseline, "hinted": hinted}, f, ensure_ascii=False, indent=2)
    print(f"\nSaved results: {out_path}")

    # Quick verdict
    print("\n=== VERDICT ===")
    for r in (baseline, hinted):
        bk_leak = any("booking_id" in nc for nc in r.get("nl2code_outputs", []))
        print(
            f"  {r['label']}: status={r.get('status')} low_conf={r.get('low_conf')} "
            f"booking_id_leak={bk_leak} nl2code_attempts={len(r.get('nl2code_outputs', []))}"
        )


if __name__ == "__main__":
    main()
