"""User-reported prompt probe for live Travel_Ontology_DA_v2.

Captures DA behavior for the EXACT prompts the marketing user is typing
into the web UI today (2026-05-02), so we can tell whether DA itself is
returning rich grounded answers or polite refusals — independent of the
matcher / SQL fallback.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.agents.data_search import (  # noqa: E402
    _is_low_confidence_data_agent_answer,
    _has_grounded_metrics,
    _has_yen_amount,
    _has_count_metric,
    _select_data_agent_answer,
)

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
    f"/dataagents/{DA_V2_ID}/aiassistant/openai"
)

PROMPTS = [
    # User's actual web UI prompts (2026-05-02 reports)
    ("USER1", "夏のハワイ学生旅行向けプランを企画して"),
    # Phase 10 baseline cross-reference
    ("REF1", "夏のハワイ向けの売上を教えてください"),
    # Direct sales intent (clean filter combination)
    ("USER2", "夏のハワイ学生旅行向けの売上・予約数・旅行者数・平均評価を教えてください"),
]

TIMEOUT_S = 240


def get_token() -> str:
    import subprocess
    r = subprocess.run(
        [
            "az", "account", "get-access-token",
            "--resource", "https://analysis.windows.net/powerbi/api",
            "--query", "accessToken", "-o", "tsv",
        ],
        capture_output=True, text=True, shell=True, check=True,
    )
    return r.stdout.strip()


def make_client(token: str):
    from openai import OpenAI

    activity_id = str(uuid.uuid4())
    return OpenAI(
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
        timeout=TIMEOUT_S,
    )


def run_one(token: str, qid: str, question: str) -> dict:
    t0 = time.time()
    client = make_client(token)
    out: dict = {"qid": qid, "question": question}
    try:
        assistant = client.beta.assistants.create(model="not used")
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id, role="user", content=question,
        )
        run = client.beta.threads.runs.create(
            thread_id=thread.id, assistant_id=assistant.id,
        )

        terminal = {"completed", "failed", "cancelled", "requires_action", "expired"}
        deadline = time.time() + TIMEOUT_S
        while run.status not in terminal:
            if time.time() > deadline:
                break
            time.sleep(2)
            run = client.beta.threads.runs.retrieve(
                thread_id=thread.id, run_id=run.id,
            )
        out["run_status"] = run.status
        out["run_last_error"] = (
            None if not getattr(run, "last_error", None)
            else str(run.last_error)
        )

        msgs = client.beta.threads.messages.list(thread_id=thread.id, order="asc")
        assistant_chunks: list[str] = []
        for m in msgs:
            if m.role == "assistant":
                parts: list[str] = []
                for c in m.content:
                    if hasattr(c, "text"):
                        parts.append(c.text.value)
                joined = "\n".join(parts).strip()
                if joined:
                    assistant_chunks.append(joined)
        out["assistant_messages_count"] = len(assistant_chunks)
        final_answer = _select_data_agent_answer(assistant_chunks)
        out["final_answer_len"] = len(final_answer)
        out["final_answer_full"] = final_answer
        out["matcher"] = {
            "low_confidence": _is_low_confidence_data_agent_answer(final_answer),
            "has_grounded_metrics": _has_grounded_metrics(final_answer),
            "has_yen": _has_yen_amount(final_answer),
            "has_count": _has_count_metric(final_answer),
        }

        try:
            client.beta.threads.delete(thread.id)
        except Exception:
            pass
    except Exception as ex:
        out["exception"] = f"{type(ex).__name__}:{ex}"
    out["elapsed_s"] = round(time.time() - t0, 1)
    return out


def main() -> int:
    token = get_token()
    results: list[dict] = []
    for qid, q in PROMPTS:
        print(f"\n=== {qid}: {q}")
        r = run_one(token, qid, q)
        results.append(r)
        status = r.get("run_status") or r.get("exception") or "unknown"
        ans_len = r.get("final_answer_len", 0)
        m = r.get("matcher", {})
        print(
            f"  status={status} elapsed={r['elapsed_s']}s "
            f"answer_len={ans_len} "
            f"low_conf={m.get('low_confidence')} "
            f"grounded={m.get('has_grounded_metrics')} "
            f"yen={m.get('has_yen')} count={m.get('has_count')}"
        )
        ans = r.get("final_answer_full", "")
        if ans:
            preview = ans[:500].replace("\n", " | ")
            print(f"  preview: {preview}")
    out_path = Path(__file__).parent / "probe_user_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
