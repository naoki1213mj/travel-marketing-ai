"""Probe ontology v2 booking EntityType to understand the time-series gap."""
import subprocess, requests, time, base64, json

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
        res = rrr.json()
        parts = res["definition"]["parts"]
        print(f"parts: {len(parts)}")
        for p in parts:
            print(f"  - {p['path']}")
        for p in parts:
            if p["path"].endswith("/definition.json") and "EntityTypes" in p["path"]:
                content = base64.b64decode(p["payload"]).decode("utf-8")
                obj = json.loads(content)
                disp = obj.get("displayName") or obj.get("name") or "?"
                if "booking" in disp.lower() or "booking" in str(obj).lower()[:500]:
                    print()
                    print(f"=== {p['path']} -> {disp}")
                    print("Top-level keys:", list(obj.keys()))
                    with open("booking_entity.json", "w", encoding="utf-8") as fh:
                        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))
                    def walk(o, path=""):
                        if isinstance(o, dict):
                            for k, v in o.items():
                                if "time" in k.lower() or "series" in k.lower() or "indicator" in k.lower():
                                    print(f"  TIME-KEY {path}.{k} = {json.dumps(v, ensure_ascii=False)[:300]}")
                                walk(v, path + "." + k)
                        elif isinstance(o, list):
                            for idx, x in enumerate(o):
                                walk(x, path + f"[{idx}]")
                    walk(obj, "")
                    if "properties" in obj:
                        print(f"  property count: {len(obj['properties'])}")
                        for pr in obj["properties"]:
                            if pr.get("id") in ("total_revenue_jpy", "departure_date", "booking_date", "price_per_person_jpy", "pax"):
                                print(f"    {pr.get('id')}: keys={list(pr.keys())}")
                                print(f"      detail: {json.dumps(pr, ensure_ascii=False)[:400]}")
                    break
        break
