"""Demo 4-prompt smoke test against Travel_Ontology_DA_v2.

After patch_demo_few_shot.py adds §E.demo Few-Shot examples for the marketing
demo's 4 prompts, run this to verify each combo returns grounded answers (not
"実績データなし").

Usage:
    uv run python scripts/fabric_data_overhaul/v2_artifacts/smoke_demo_prompts.py

Reuses grade()/run_one()/get_token() from smoke_test_v6.py for consistent
grading semantics with the 14-prompt baseline.

Demo prompts (region + season + customer_segment composite filter):
    D01: 春の沖縄ファミリー向けプランを企画して
    D02: 冬の北海道カップル向けプランを企画して
    D03: 秋の京都シニア向けプランを企画して
    D04: 夏のハワイ学生向けプランを企画して

Target: 4/4 grade A. Output: smoke_demo_results.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Reuse helpers from smoke_test_v6 (same directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import bestof_strict  # type: ignore  # noqa: E402
import smoke_test_v6  # type: ignore  # noqa: E402

DEMO_PROMPTS = [
    ("D01", "春の沖縄ファミリー向けプランを企画して"),
    ("D02", "冬の北海道カップル向けプランを企画して"),
    ("D03", "秋の京都シニア向けプランを企画して"),
    ("D04", "夏のハワイ学生向けプランを企画して"),
]


def main() -> int:
    results = []
    for qid, q in DEMO_PROMPTS:
        print(f"\n=== {qid}: {q}")
        r = smoke_test_v6.run_one(None, qid, q)
        # `run_one` returns a dict keyed off the qid passed in; defensively ensure
        # the result carries an "id" entry so downstream gating lookups work even
        # if the helper's schema drifts.
        r.setdefault("id", qid)
        # Apply strict grader (bestof_strict.grade2) on top of v6 grader to catch
        # false-A from no-data narratives that contain incidental small numbers.
        strict_grade, strict_reason = bestof_strict.grade2(r.get("answer") or "")
        r["strict_grade"] = strict_grade
        r["strict_reason"] = strict_reason
        ans_preview = (r["answer"] or "")[:400].replace("\n", " ")
        print(
            f"  -> status={r['status']} v6_grade={r['grade']} ({r['reason']}) "
            f"strict_grade={strict_grade} ({strict_reason}) {r['elapsed_s']}s"
        )
        if r["answer"]:
            print(f"  preview: {ans_preview}")
        if r.get("last_error"):
            print(f"  last_error: {r['last_error']}")
        results.append(r)

    out = Path(__file__).parent / "smoke_demo_results.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to {out}")

    print("\n=== DEMO SUMMARY (v6 grader) ===")
    a = sum(1 for r in results if r["grade"] == "A")
    b = sum(1 for r in results if r["grade"] == "B")
    c = sum(1 for r in results if r["grade"] == "C")
    total = len(results)
    print(f"A (grounded): {a}/{total}")
    print(f"B (coherent): {b}/{total}")
    print(f"C (failed):   {c}/{total}")

    print("\n=== DEMO SUMMARY (strict grader) ===")
    sa = sum(1 for r in results if r["strict_grade"] == "A")
    sb = sum(1 for r in results if r["strict_grade"] == "B")
    sc = sum(1 for r in results if r["strict_grade"] == "C")
    print(f"A (grounded): {sa}/{total}")
    print(f"B (coherent): {sb}/{total}")
    print(f"C (failed):   {sc}/{total}")

    # Strict grader is the authoritative success criterion (Blocking #1 from rubber-duck).
    # User-reported demo prompt 「春の沖縄ファミリー」 (D01) MUST be strict A; the rest
    # are tracked for awareness but not gating.
    d01 = next((r for r in results if r["id"] == "D01"), None)
    d01_strict_a = d01 is not None and d01.get("strict_grade") == "A"
    print(
        f"\nGating check D01 strict A: "
        f"{'✅ MET' if d01_strict_a else '❌ NOT MET (User report not resolved)'}"
    )
    print(f"Strict A overall: {sa}/{total} (informational)")
    return 0 if d01_strict_a else 1


if __name__ == "__main__":
    sys.exit(main())
