"""Probe the live Travel_Ontology_DA_v2 with the user's reported prompts.

Captures BOTH:
  - assistant_messages (the rendered answer)
  - run_steps tool_outputs (what _extract_data_agent_tool_outputs would extract)
  - _is_low_confidence_data_agent_answer judgement on each

Prompts: the three the user reported failing in the live UI, plus P10/P11 (known
weak points) for cross-reference.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# Ensure src on path so we can import the production low-confidence detector
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.agents.data_search import (  # noqa: E402
    _DATA_AGENT_RESULT_TOOL_NAMES,
    _is_low_confidence_data_agent_answer,
    _select_data_agent_answer,
)

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
    f"/dataagents/{DA_V2_ID}/aiassistant/openai"
)

PROMPTS = [
    ("U1", "過去5年で人気の旅行先の売上トレンドを教えて"),
    ("U2", "夏の家族旅行プランで売れているものは？"),
    ("U3", "20代女性に人気のプラン上位3つは？"),
    ("P10", "年別の売上トレンドを教えてください"),
    ("P11", "リピート顧客の比率を教えてください"),
]

TIMEOUT_S = 240


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
        out["assistant_messages"] = assistant_chunks
        final_answer = _select_data_agent_answer(assistant_chunks)
        out["final_answer"] = final_answer
        out["final_low_confidence"] = _is_low_confidence_data_agent_answer(final_answer)

        try:
            steps = client.beta.threads.runs.steps.list(
                thread_id=thread.id, run_id=run.id, order="asc",
            )
        except Exception as ex:  # pragma: no cover - diagnostics
            steps = None
            out["steps_error"] = f"{type(ex).__name__}:{ex}"

        tool_outputs: list[dict] = []
        if steps is not None:
            for step in getattr(steps, "data", []) or []:
                step_details = getattr(step, "step_details", None)
                for tool_call in getattr(step_details, "tool_calls", []) or []:
                    fn = getattr(tool_call, "function", None)
                    name = str(getattr(fn, "name", "") or "")
                    raw_output = str(getattr(fn, "output", "") or "")
                    tool_outputs.append({
                        "tool_name": name,
                        "is_extracted": name in _DATA_AGENT_RESULT_TOOL_NAMES,
                        "output_len": len(raw_output),
                        "output_preview": raw_output[:600],
                        "low_confidence": _is_low_confidence_data_agent_answer(raw_output),
                        "has_failed_to_generate": bool(
                            re.search(r"failed to generate", raw_output, re.I)
                        ),
                        "has_nl2ontology": bool(
                            re.search(r"nl2ontology", raw_output, re.I)
                        ),
                    })
        out["tool_outputs"] = tool_outputs

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
        ans = r.get("final_answer") or ""
        lc = r.get("final_low_confidence")
        print(
            f"  status={status} elapsed={r['elapsed_s']}s "
            f"final_lc={lc} answer_len={len(ans)}"
        )
        for to in r.get("tool_outputs", []):
            print(
                f"    tool={to['tool_name']} extracted={to['is_extracted']} "
                f"len={to['output_len']} lc={to['low_confidence']} "
                f"failed_gen={to['has_failed_to_generate']} "
                f"nl2={to['has_nl2ontology']}"
            )
        if ans:
            print(f"  preview: {ans[:300].replace(chr(10), ' ')}")
    out_path = Path(__file__).parent / "probe_live_results.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nResults: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
