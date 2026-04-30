"""Survey timeseriesProperties across all 10 entities."""
import json, base64, subprocess, requests, time

W = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
O = "10cd6675-405a-4366-b91b-d57242a28914"
F = "https://api.fabric.microsoft.com"

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", F, "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
h = {"Authorization": f"Bearer {t}"}

r = requests.post(f"{F}/v1/workspaces/{W}/ontologies/{O}/getDefinition", headers=h)
loc = r.headers["Location"]
for i in range(60):
    time.sleep(2)
    rr = requests.get(loc, headers=h)
    if rr.status_code == 200 and rr.json().get("status") == "Succeeded":
        rrr = requests.get(loc + "/result", headers=h)
        parts = rrr.json()["definition"]["parts"]
        for p in parts:
            if p["path"].endswith("/definition.json") and "EntityTypes" in p["path"]:
                obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
                name = obj.get("name", "?")
                tsp = obj.get("timeseriesProperties", [])
                date_props = [pr for pr in obj.get("properties", []) if pr.get("valueType") == "DateTime"]
                num_props = [pr for pr in obj.get("properties", []) if pr.get("valueType") in ("BigInt", "Double", "Decimal")]
                print(f"{name:20s} timeseries={len(tsp)} dateprops={[p['name'] for p in date_props]} numprops={[p['name'] for p in num_props]}")
                if tsp:
                    print(f"  sample: {json.dumps(tsp[0], ensure_ascii=False)}")
        break
