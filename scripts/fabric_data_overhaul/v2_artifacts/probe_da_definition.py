"""Probe current shape of Travel_Ontology_DA_v2 definition."""
import subprocess, requests, time, json
WORKSPACE = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
FABRIC = "https://api.fabric.microsoft.com"

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", FABRIC, "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
h = {"Authorization": f"Bearer {t}"}

url = f"{FABRIC}/v1/workspaces/{WORKSPACE}/dataAgents/{DA_ID}/getDefinition"
r = requests.post(url, headers=h)
print(f"POST getDefinition -> {r.status_code}")
print(f"  Location: {r.headers.get('Location')}")
print(f"  Retry-After: {r.headers.get('Retry-After')}")

if r.status_code == 202:
    loc = r.headers["Location"]
    for i in range(60):
        time.sleep(2)
        rr = requests.get(loc, headers=h)
        if rr.status_code == 200:
            d = rr.json()
            s = d.get("status")
            print(f"  poll[{i}] status={s}")
            if s == "Succeeded":
                rrr = requests.get(loc + "/result", headers=h)
                print(f"  GET result HTTP {rrr.status_code}")
                if rrr.status_code == 200:
                    res = rrr.json()
                    parts = res.get("definition", {}).get("parts", [])
                    print(f"  parts: {len(parts)}")
                    for p in parts:
                        print(f"    - {p['path']}")
                break
            if s in ("Failed", "Cancelled"):
                print(json.dumps(d, indent=2))
                break
elif r.status_code == 200:
    print(json.dumps(r.json(), indent=2)[:1500])
else:
    print(f"  Body: {r.text[:1500]}")
