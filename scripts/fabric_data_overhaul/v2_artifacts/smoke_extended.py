"""Re-run P13/P14 only with 600s timeout."""
import json, subprocess, time, uuid

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/dataagents/{DA_V2_ID}/aiassistant/openai"
TIMEOUT_S = 600

PROMPTS = [
    ("P13", "円安後の海外売上回復の度合いを教えてください"),
    ("P14", "インバウンド比率の四半期推移を教えてください"),
]


def get_token():
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "https://analysis.windows.net/powerbi/api", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True
    )
    return r.stdout.strip()


def make_client(token):
    from openai import OpenAI
    return OpenAI(
        base_url=BASE_URL, api_key="dummy",
        default_headers={"Authorization": f"Bearer {token}", "ActivityId": str(uuid.uuid4())},
    )


import re
_FAIL = ("申し訳ありません", "システムの都合", "再度ご依頼", "技術的制約", "実行できませんでした", "一時的に")
_NUM = re.compile(r"[0-9][\d,]*\s*(?:円|万|億|%|件|人)|¥|¥|JPY")
_NODATA = ("0件", "データなし", "見つかりませんでした")


def grade(answer):
    if not answer:
        return "C", "no_answer"
    if any(p in answer for p in _FAIL) and not _NUM.search(answer):
        return "C", "fail_phrase"
    if any(p in answer for p in _NODATA) and not _NUM.search(answer):
        return "C", "no_data_no_grounding"
    if _NUM.search(answer):
        return "A", "grounded_numeric"
    return "B", "coherent_no_numbers"


def run_one(qid, q):
    t0 = time.time()
    tok = get_token()
    c = make_client(tok)
    a = c.beta.assistants.create(model="not used")
    th = c.beta.threads.create()
    c.beta.threads.messages.create(thread_id=th.id, role="user", content=q)
    run = c.beta.threads.runs.create(thread_id=th.id, assistant_id=a.id)
    terminal = {"completed", "failed", "cancelled", "requires_action", "expired"}
    deadline = time.time() + TIMEOUT_S
    while run.status not in terminal:
        if time.time() > deadline:
            break
        time.sleep(3)
        run = c.beta.threads.runs.retrieve(thread_id=th.id, run_id=run.id)
    if run.status != "completed":
        try: c.beta.threads.delete(th.id)
        except: pass
        return {"qid": qid, "question": q, "status": run.status, "answer": None,
                "grade": "C", "reason": f"run.{run.status}",
                "elapsed_s": round(time.time() - t0, 1)}
    msgs = c.beta.threads.messages.list(thread_id=th.id, order="asc")
    chunks = []
    for m in msgs:
        if m.role == "assistant":
            for cc in m.content:
                if hasattr(cc, "text"):
                    chunks.append(cc.text.value)
    ans = "\n".join(chunks).strip() or None
    try: c.beta.threads.delete(th.id)
    except: pass
    g, r = grade(ans)
    return {"qid": qid, "question": q, "status": "completed", "answer": ans,
            "grade": g, "reason": r, "elapsed_s": round(time.time() - t0, 1)}


results = []
for qid, q in PROMPTS:
    print(f"=== {qid}: {q}", flush=True)
    r = run_one(qid, q)
    print(f"  -> {r['status']} grade={r['grade']} {r['elapsed_s']}s", flush=True)
    if r["answer"]:
        print(f"  preview: {r['answer'][:400]}", flush=True)
    results.append(r)

with open("smoke_results_v6_extended.json", "w", encoding="utf-8") as fh:
    json.dump(results, fh, ensure_ascii=False, indent=2)
a = sum(1 for r in results if r["grade"] == "A")
print(f"\n=== {a}/{len(results)} A ===")
