"""Phase 9.6 — 14-prompt smoke test against Travel_Ontology_DA_v2 (updated aiInstructions).

Differences vs smoke_test_v2.py (v5 baseline):
- TIMEOUT_S bumped 180 → 300 (P10/P14 timed out at 180)
- Tighter grader: '0件' / 'データなし' / '見つかりませんでした' with no real ¥-amount → C
- Captures run.steps on non-completed runs so we can see which tool call failed
  (used for diagnosing P10/P14 submit_tool_outputs BadRequest in v5)
- Output: smoke_results_v6.json
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/dataagents/{DA_V2_ID}/aiassistant/openai"

PROMPTS = [
    ("P01", "ハワイの売上を教えてください"),
    ("P02", "夏のハワイの売上を教えてください"),
    ("P03", "ハワイで20代の旅行者の売上を教えてください"),
    ("P04", "夏のハワイで20代の旅行者の売上を教えてください"),
    ("P05", "夏のハワイで20代の旅行者の売上、予約数、平均評価を教えてください"),
    ("P06", "ハワイのレビュー評価分布を教えてください"),
    ("P07", "夏の沖縄でファミリー向けの売上を教えてください"),
    ("P08", "春のパリの売上を教えてください"),
    ("P09", "旅行先別の売上ランキングを教えてください"),
    ("P10", "年別の売上トレンドを教えてください"),
    ("P11", "リピート顧客の比率を教えてください"),
    ("P12", "キャンセル率が高いプラン上位5位は？"),
    ("P13", "円安後の海外売上回復の度合いを教えてください"),
    ("P14", "インバウンド比率の四半期推移を教えてください"),
]

TIMEOUT_S = 300  # was 180 in v5 — multi-table queries need >180s


def get_token() -> str:
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "https://analysis.windows.net/powerbi/api", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True
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


_NO_DATA_PHRASES = [
    "データなし",
    "0件",
    "0 件",
    "該当データがありません",
    "該当データはありません",
    "該当する取引が見つかりませんでした",
    "見つかりませんでした",
    "見つかりません",
    "該当なし",
]

_FAIL_PHRASES = [
    "技術的なエラー",
    "システム的なエラー",
    "システム的な制約",
    "システムの都合で",
    "システムの都合により",
    "技術的制約",
    "技術的な都合",
    "データ抽出ができませんでした",
    "集計クエリの制約により",
    "グループ集計の制約により",
    "取得できませんでした",
    "情報の取得が一時的にできませんでした",
    "再度ご依頼いただけましたら",
    "再度お試しください",
    "もう一度お試しください",
    "申し訳ありません",
    "お答えできません",
    "申し訳ございません",
    "確認できませんでした",
    "ツール側制限",
    "ツール側の制限",
    "SM側で計算列が見えない",
    "計算列が見えない",
    "一時的に",  # 強い兆候。"一時的にできませんでした" が頻出
]

_PLACEHOLDER_PHRASES = [
    "旅行先A", "旅行先B", "○○件", "○○○○", "X/XX/XXX",
    "プレースホルダー",
]


def grade(answer: str | None) -> tuple[str, str]:
    """Return (grade, reason).

    Grade C: empty, fail-phrase, placeholder, or no-data without numeric grounding.
    Grade B: coherent prose but no numeric grounding.
    Grade A: contains real numeric grounding (¥-amount > 0, percent, or 2+ digit count).
    """
    if answer is None or not answer.strip():
        return "C", "empty"
    a = answer

    for p in _FAIL_PHRASES:
        if p in a:
            return "C", f"fail_phrase:{p}"
    for p in _PLACEHOLDER_PHRASES:
        if p in a:
            return "C", f"placeholder:{p}"

    # Real numeric grounding checks
    yen_amounts = re.findall(r"[¥￥]\s*([\d,]+)", a)
    yen_amounts += re.findall(r"([\d,]+)\s*円", a)
    yen_values = [int(s.replace(",", "")) for s in yen_amounts if s.replace(",", "").isdigit()]
    has_real_yen = any(v >= 1000 for v in yen_values)  # at least ¥1,000

    pct_values = [float(m.group(1)) for m in re.finditer(r"(\d+(?:\.\d+)?)\s*%", a)]
    has_real_pct = any(0 < v <= 100 for v in pct_values)

    digit_runs = re.findall(r"\d{2,}", a)
    has_real_count = any(int(s) > 0 for s in digit_runs)

    grounded = has_real_yen or has_real_pct or has_real_count

    # No-data answer: must have BOTH a no-data phrase AND no real numeric grounding → C
    no_data_present = any(p in a for p in _NO_DATA_PHRASES)
    if no_data_present and not grounded:
        return "C", "no_data_no_grounding"
    # If "0件" appears but there ARE real ¥ amounts, treat as A (likely partial result)
    if grounded:
        return "A", "grounded_numeric"

    if len(a.strip()) > 100:
        return "B", "coherent_but_not_numeric"
    return "C", "too_short"


def fetch_run_steps(client, thread_id: str, run_id: str) -> list[dict]:
    """Get step list for diagnostics on failed runs."""
    try:
        steps = client.beta.threads.runs.steps.list(thread_id=thread_id, run_id=run_id)
        out = []
        for s in steps:
            entry: dict = {
                "id": s.id,
                "status": getattr(s, "status", None),
                "type": getattr(s, "type", None),
                "step_type": getattr(getattr(s, "step_details", None), "type", None),
            }
            sd = getattr(s, "step_details", None)
            if sd:
                if hasattr(sd, "tool_calls"):
                    entry["tool_calls"] = []
                    for tc in (sd.tool_calls or []):
                        tc_entry = {
                            "type": getattr(tc, "type", None),
                            "id": getattr(tc, "id", None),
                        }
                        # Best-effort serialization of the tool input/output
                        for attr in ("function", "code_interpreter", "retrieval"):
                            v = getattr(tc, attr, None)
                            if v:
                                try:
                                    tc_entry[attr] = json.loads(json.dumps(v, default=lambda o: getattr(o, "__dict__", str(o))))[:5000] if isinstance(v, str) else json.loads(json.dumps(v, default=lambda o: getattr(o, "__dict__", str(o))))
                                except Exception:
                                    tc_entry[attr] = str(v)[:2000]
                        entry["tool_calls"].append(tc_entry)
            le = getattr(s, "last_error", None)
            if le:
                entry["last_error"] = {
                    "code": getattr(le, "code", None),
                    "message": getattr(le, "message", None),
                }
            out.append(entry)
        return out
    except Exception as ex:
        return [{"_error": f"steps_fetch_failed:{ex}"}]


def run_one(token: str | None, qid: str, question: str) -> dict:
    t0 = time.time()
    # Always refresh token per prompt to avoid 1-hr expiry mid-run
    fresh_token = get_token()
    client = make_client(fresh_token)
    try:
        assistant = client.beta.assistants.create(model="not used")
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id, role="user", content=question
        )
        run = client.beta.threads.runs.create(
            thread_id=thread.id, assistant_id=assistant.id
        )

        terminal = {"completed", "failed", "cancelled", "requires_action", "expired"}
        deadline = time.time() + TIMEOUT_S
        while run.status not in terminal:
            if time.time() > deadline:
                break
            time.sleep(2)
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

        if run.status != "completed":
            err = getattr(run, "last_error", None)
            err_dict = None
            if err:
                err_dict = {
                    "code": getattr(err, "code", None),
                    "message": getattr(err, "message", None),
                }
            steps = fetch_run_steps(client, thread.id, run.id)
            try:
                client.beta.threads.delete(thread.id)
            except Exception:
                pass
            return {
                "qid": qid, "question": question, "status": run.status,
                "answer": None, "grade": "C",
                "reason": f"run.{run.status}",
                "last_error": err_dict,
                "steps": steps,
                "elapsed_s": round(time.time() - t0, 1),
            }

        msgs = client.beta.threads.messages.list(thread_id=thread.id, order="asc")
        assistant_chunks = []
        for m in msgs:
            if m.role == "assistant":
                parts = []
                for c in m.content:
                    if hasattr(c, "text"):
                        parts.append(c.text.value)
                joined = "\n".join(parts).strip()
                if joined:
                    assistant_chunks.append(joined)
        answer = assistant_chunks[-1] if assistant_chunks else None

        try:
            client.beta.threads.delete(thread.id)
        except Exception:
            pass

        g, reason = grade(answer)
        return {
            "qid": qid, "question": question, "status": "completed",
            "answer": answer, "grade": g, "reason": reason,
            "elapsed_s": round(time.time() - t0, 1),
        }
    except Exception as ex:
        return {
            "qid": qid, "question": question, "status": "exception",
            "answer": None, "grade": "C",
            "reason": f"exception:{type(ex).__name__}:{ex}",
            "elapsed_s": round(time.time() - t0, 1),
        }


def main():
    results = []
    # Optional CLI args: prompt IDs (e.g. P01 P08 P10) or none for full run
    only_set = set(a for a in sys.argv[1:] if a.startswith("P"))
    out_name = "smoke_results_v6.json"
    for a in sys.argv[1:]:
        if a.startswith("--out="):
            out_name = a.split("=", 1)[1]
    prompts = [(q, t) for q, t in PROMPTS if (not only_set or q in only_set)]
    for qid, q in prompts:
        print(f"\n=== {qid}: {q}")
        r = run_one(None, qid, q)
        ans_preview = (r["answer"] or "")[:400].replace("\n", " ")
        print(f"  -> status={r['status']} grade={r['grade']} ({r['reason']}) {r['elapsed_s']}s")
        if r["answer"]:
            print(f"  preview: {ans_preview}")
        if r.get("last_error"):
            print(f"  last_error: {r['last_error']}")
        results.append(r)

    out = Path(__file__).parent / out_name
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to {out}")

    print("\n=== SUMMARY ===")
    a = sum(1 for r in results if r["grade"] == "A")
    b = sum(1 for r in results if r["grade"] == "B")
    c = sum(1 for r in results if r["grade"] == "C")
    total = len(results)
    print(f"A (grounded): {a}/{total}")
    print(f"B (coherent): {b}/{total}")
    print(f"C (failed):   {c}/{total}")
    target_met = a >= 12 if total == 14 else a >= total
    print(f"\nTarget >=12/14 grade A: {'MET' if target_met else f'NOT MET ({a}/{total})'}")
    return 0 if target_met else 1


if __name__ == "__main__":
    sys.exit(main())
