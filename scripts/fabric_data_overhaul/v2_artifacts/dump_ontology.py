"""Dump full ontology getDefinition result so we can patch in place."""
import json, base64, subprocess, requests, time, sys

W = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
O = "10cd6675-405a-4366-b91b-d57242a28914"
F = "https://api.fabric.microsoft.com"

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", F, "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
h = {"Authorization": f"Bearer {t}"}

r = requests.post(f"{F}/v1/workspaces/{W}/ontologies/{O}/getDefinition", headers=h)
print("POST status:", r.status_code, "Location:", r.headers.get("Location"))
loc = r.headers["Location"]
for i in range(60):
    time.sleep(2)
    rr = requests.get(loc, headers=h)
    if rr.status_code == 200:
        st = rr.json().get("status")
        if st == "Succeeded":
            rrr = requests.get(loc + "/result", headers=h)
            data = rrr.json()
            break
        elif st in ("Failed", "Canceled"):
            print("LRO failed:", rr.json())
            sys.exit(2)
else:
    print("LRO timeout"); sys.exit(2)

with open("ontology_full.json", "w", encoding="utf-8") as fh:
    json.dump(data, fh, ensure_ascii=False, indent=2)
parts = data["definition"]["parts"]
print(f"Saved {len(parts)} parts to ontology_full.json")
print("\nPart paths:")
for p in parts:
    print(" ", p["path"])
