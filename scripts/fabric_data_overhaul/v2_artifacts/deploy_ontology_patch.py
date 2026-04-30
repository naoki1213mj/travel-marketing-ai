"""Deploy patched ontology via updateDefinition LRO."""
import json, subprocess, requests, time, sys

W = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
O = "10cd6675-405a-4366-b91b-d57242a28914"
F = "https://api.fabric.microsoft.com"

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", F, "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
h = {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}

with open("ontology_patched.json", "r", encoding="utf-8") as fh:
    body = json.load(fh)

print(f"POST updateDefinition: {len(body['definition']['parts'])} parts")
r = requests.post(
    f"{F}/v1/workspaces/{W}/ontologies/{O}/updateDefinition",
    headers=h,
    json=body,
)
print(f"Status: {r.status_code}")
if r.status_code not in (200, 201, 202):
    print("Body:", r.text[:2000])
    sys.exit(2)

if r.status_code == 202:
    loc = r.headers.get("Location")
    print(f"LRO at {loc}")
    for i in range(120):
        time.sleep(2)
        rr = requests.get(loc, headers=h)
        if rr.status_code != 200:
            print(f"poll {i}: {rr.status_code} {rr.text[:500]}")
            continue
        st = rr.json().get("status")
        print(f"poll {i}: {st}")
        if st == "Succeeded":
            print("SUCCESS")
            try:
                rrr = requests.get(loc + "/result", headers=h)
                print("Result:", rrr.text[:500])
            except Exception:
                pass
            sys.exit(0)
        if st in ("Failed", "Canceled"):
            print("FAILED:")
            print(json.dumps(rr.json(), ensure_ascii=False, indent=2))
            sys.exit(2)
    print("LRO timed out")
    sys.exit(2)

print("Sync result:", r.text[:500])
