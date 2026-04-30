"""Inspect run.steps for the after-ontology batch."""
import json

with open("smoke_results_v6_after_ontology.json", "r", encoding="utf-8") as fh:
    data = json.load(fh)

for r in data:
    pid = r["id"]
    print(f"\n=== {pid} grade={r.get('grade')} reason={r.get('reason')} status={r.get('status')}")
    print(f"    last_error: {r.get('last_error')}")
    steps = r.get("steps") or []
    for s in steps:
        sd = s.get("step_details", {}) or {}
        tcs = sd.get("tool_calls", []) or []
        for tc in tcs:
            tn = tc.get("type", "?")
            sub = tc.get(tn, {}) if isinstance(tc.get(tn), dict) else {}
            inp = sub.get("input", "") or sub.get("arguments", "") or sub.get("query", "")
            inp = str(inp)[:300]
            out = str(sub.get("output", ""))[:200]
            le = s.get("last_error")
            sid = s.get("id", "?")[-6:]
            print(f"  step {sid} status={s.get('status')} tool={tn}")
            if inp: print(f"    input={inp!r}")
            if out: print(f"    output={out!r}")
            if le: print(f"    err={le}")
