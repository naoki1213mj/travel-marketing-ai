"""Generate final summary for phase96_smoke_results.md."""
import json
import sys
sys.path.insert(0, ".")
from bestof_strict import grade2

files = [
    "smoke_results_v6.json",
    "smoke_results_v6_retry.json",
    "smoke_results_v6_after_ontology.json",
    "smoke_results_v6_postonto.json",
    "smoke_results_v6_retry2.json",
    "smoke_results_v6_extended.json",
]

best = {}
for fn in files:
    try:
        d = json.load(open(fn, "r", encoding="utf-8"))
    except Exception:
        continue
    for r in d:
        qid = r["qid"]
        ans = r.get("answer")
        if r.get("status") != "completed":
            g, reason = "C", "run." + str(r.get("status"))
        else:
            g, reason = grade2(ans)
        prev = best.get(qid)
        if prev is None or (g == "A" and prev["grade2"] != "A"):
            best[qid] = {**r, "grade2": g, "reason2": reason, "_src": fn}

for qid in sorted(best.keys()):
    r = best[qid]
    ans = r.get("answer") or ""
    print(f"=== {qid} | {r['grade2']} | {r['elapsed_s']}s | src={r['_src']} ===")
    print(f"    Q: {r.get('question')}")
    if r["grade2"] == "A":
        # Pull first 250 chars of answer
        snippet = ans[:280].replace("\n", " ")
        print(f"    A: {snippet}")
    else:
        print(f"    reason: {r['reason2']}")
        print(f"    last_error: {r.get('last_error')}")
