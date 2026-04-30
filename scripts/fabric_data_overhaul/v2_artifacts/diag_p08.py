"""Re-run P08 with full step capture to see why it reports no data despite 192 confirmed bookings.
Always captures runs.steps regardless of completion status."""
import json, subprocess, time, uuid
from pathlib import Path

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
BASE_URL = f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}/dataagents/{DA_V2_ID}/aiassistant/openai"

QID = "P08"
import sys
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "春のパリの売上を教えてください"


def get_token() -> str:
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource",
         "https://analysis.windows.net/powerbi/api", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, shell=True, check=True
    )
    return r.stdout.strip()


def make_client(token: str):
    from openai import OpenAI
    return OpenAI(
        base_url=BASE_URL,
        api_key="dummy",
        default_headers={
            "Authorization": f"Bearer {token}",
            "ActivityId": str(uuid.uuid4()),
        },
    )


tok = get_token()
client = make_client(tok)
assistant = client.beta.assistants.create(model="not used")
thread = client.beta.threads.create()
client.beta.threads.messages.create(thread_id=thread.id, role="user", content=QUESTION)
run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)

t0 = time.time()
terminal = {"completed", "failed", "cancelled", "requires_action", "expired"}
while run.status not in terminal:
    if time.time() - t0 > 300:
        break
    time.sleep(2)
    run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)

print(f"Run status: {run.status} after {time.time()-t0:.1f}s")

# Capture steps regardless of status
try:
    steps = client.beta.threads.runs.steps.list(thread_id=thread.id, run_id=run.id)
    out = []
    for s in steps.data:
        d = s.model_dump()
        out.append(d)
    Path("p08_steps.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {len(out)} steps -> p08_steps.json")
    # Brief summary
    for s in out:
        sd = s.get("step_details", {}) or {}
        tcs = sd.get("tool_calls", []) or []
        for tc in tcs:
            tn = tc.get("type", "?")
            tcd = tc.get(tn, {})
            print(f"\n[{s.get('status')}] tool={tn} step={s.get('id')[-6:]}")
            if isinstance(tcd, dict):
                inp = tcd.get("input") or tcd.get("query") or tcd.get("arguments")
                outp = tcd.get("output")
                if inp:
                    print(f"  INPUT:  {str(inp)[:600]}")
                if outp:
                    print(f"  OUTPUT: {str(outp)[:300]}")
except Exception as ex:
    print(f"steps fetch failed: {ex}")

# Final answer
msgs = client.beta.threads.messages.list(thread_id=thread.id, order="asc")
for m in msgs:
    if m.role == "assistant":
        for c in m.content:
            if hasattr(c, "text"):
                print(f"\n=== ANSWER ===\n{c.text.value}")
client.beta.threads.delete(thread.id)
