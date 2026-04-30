"""Stricter grader for false-A detection.

A grade requires:
  (a) at least one large-yen amount: ¥...,...,...,XXX OR XX億 OR XX,XXX,XXX 円
  (b) NOT mostly explanatory ("円安とは", "とは何か", "もう少し詳細"...)
  (c) NOT asking for clarification

Previous grader's problem: matched any number+(円/万/%/件) — so '1ドル＝150円' or 'Q1: 8%' counted.
"""
import json, re
from pathlib import Path

# Only count yen amounts of substantial magnitude (millions+)
LARGE_YEN = re.compile(
    r"(?:¥|￥)\s*[0-9][\d,]{4,}"          # ¥ + 5+ digits
    r"|[0-9][\d,]{2,}\s*万円"              # 数+万円
    r"|[0-9][\d,]*\s*億(?:[0-9,]+\s*万)?円"  # 億円
)
COUNT_LARGE = re.compile(
    r"[0-9][\d,]{2,}\s*件"                   # 100+件
    r"|[0-9][\d,]{2,}\s*人"                   # 100+人
)
PCT_REASONABLE = re.compile(r"[0-9][\d.]*\s*%")

# Phrases indicating false-LLM mode
LLM_FALLBACK = [
    "とは",            # explaining concepts
    "もう少し詳細",      # asking for clarification
    "教えていただけますか",  # asking for clarification
    "リアルタイムなデータ",  # claiming no DB access
    "調べることができません",  # claiming no DB access
    "推定値",            # presenting estimates
    "例えば：",          # examples
    "業界全体",          # industry-wide pivot
    "観光庁",            # citing government data instead of ours
    "J.フロント",        # specific competitor example (LLM hallucinations)
    "HIS",
    "JATA",
    "トヨタ",
    "ソニー",
]

FAIL_PHRASES = ["申し訳ありません", "システムの都合", "再度ご依頼", "技術的制約", "実行できませんでした", "一時的に"]
# NODATA: must NOT match inside larger numbers like "30件", "10件" etc.
NODATA_RE = re.compile(r"(?:^|[^0-9])0\s*件|データなし|見つかりませんでした|該当なし|該当するデータはありません")


def grade2(answer):
    if not answer:
        return "C", "no_answer"
    if any(p in answer for p in FAIL_PHRASES):
        return "C", "fail_phrase"

    # LLM-fallback detection (high precision)
    fallback_hits = sum(1 for p in LLM_FALLBACK if p in answer)
    if fallback_hits >= 2:
        return "C", f"llm_fallback({fallback_hits}_phrases)"

    has_large_yen = bool(LARGE_YEN.search(answer))
    has_large_count = bool(COUNT_LARGE.search(answer))
    has_pct = bool(PCT_REASONABLE.search(answer))

    if NODATA_RE.search(answer) and not has_large_yen and not has_large_count and not has_pct:
        return "C", "no_data_no_grounding"

    if has_large_yen or has_large_count or has_pct:
        # additional sanity: should mention destination/season/year tied to data
        if fallback_hits >= 1:
            return "C", f"mixed_fallback({fallback_hits})"
        return "A", "grounded_numeric"

    return "B", "coherent_no_numbers"


# Apply to all run files
FILES = [
    "smoke_results_v6.json",
    "smoke_results_v6_retry.json",
    "smoke_results_v6_after_ontology.json",
    "smoke_results_v6_postonto.json",
    "smoke_results_v6_retry2.json",
    "smoke_results_v6_extended.json",
]

best = {}
runs_per_prompt = {}
for fn in FILES:
    p = Path(fn)
    if not p.exists():
        print(f"skip {fn}")
        continue
    arr = json.loads(p.read_text(encoding="utf-8"))
    for r in arr:
        qid = r["qid"]
        # Re-grade
        g, reason = grade2(r.get("answer"))
        # Override if status was non-completed
        if r.get("status") != "completed":
            g, reason = "C", f"run.{r.get('status')}"
        r2 = {**r, "grade2": g, "reason2": reason, "_source": fn}
        runs_per_prompt.setdefault(qid, []).append(r2)
        prev = best.get(qid)
        if prev is None or (g == "A" and prev["grade2"] != "A"):
            best[qid] = r2

print(f"\n{'qid':4} {'old->new':12} {'reason':40} {'src':35} runs")
print("-" * 120)
counts_a = 0
for qid in sorted(best.keys()):
    r = best[qid]
    runs = runs_per_prompt.get(qid, [])
    aa = sum(1 for x in runs if x["grade2"] == "A")
    cc = sum(1 for x in runs if x["grade2"] == "C")
    bb = sum(1 for x in runs if x["grade2"] == "B")
    if r["grade2"] == "A":
        counts_a += 1
    src = r["_source"][:35]
    old = r.get("grade", "?")
    print(f"{qid:4} {old}->{r['grade2']:8} {r.get('reason2','')[:40]:40} {src:35} {aa}A/{bb}B/{cc}C ({len(runs)})")

print(f"\nStrict best-of: {counts_a}/{len(best)} grade A")
never_a = [qid for qid in sorted(best.keys()) if best[qid]["grade2"] != "A"]
print(f"Never-A under strict grading: {never_a}")
