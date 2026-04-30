"""Best-of analysis across all smoke runs."""
import json
from pathlib import Path

FILES = [
    "smoke_results_v6.json",
    "smoke_results_v6_retry.json",
    "smoke_results_v6_after_ontology.json",
    "smoke_results_v6_postonto.json",
]

best = {}
runs_per_prompt = {}
for fn in FILES:
    p = Path(fn)
    if not p.exists():
        print(f"skip {fn} (missing)")
        continue
    arr = json.loads(p.read_text(encoding="utf-8"))
    for r in arr:
        qid = r["qid"]
        runs_per_prompt.setdefault(qid, []).append((fn, r))
        prev = best.get(qid)
        if prev is None or (r["grade"] == "A" and prev["grade"] != "A"):
            best[qid] = {**r, "_source": fn}

print(f"\n{'qid':4} {'best':6} {'reason':30} {'source':40} runs:A/B/C")
print("-" * 120)
counts_a = 0
all_qids = sorted(best.keys())
for qid in all_qids:
    r = best[qid]
    runs = runs_per_prompt.get(qid, [])
    grades = [x[1]["grade"] for x in runs]
    a = sum(1 for g in grades if g == "A")
    b = sum(1 for g in grades if g == "B")
    c = sum(1 for g in grades if g == "C")
    if r["grade"] == "A":
        counts_a += 1
    print(f"{qid:4} {r['grade']:6} {(r.get('reason') or '')[:30]:30} {r['_source']:40} {a}A/{b}B/{c}C  ({len(runs)} runs)")

print(f"\nBest-of summary: {counts_a}/{len(all_qids)} grade A")

# Identify never-A prompts
never_a = [qid for qid in all_qids if best[qid]["grade"] != "A"]
print(f"\nNever-A: {never_a}")
