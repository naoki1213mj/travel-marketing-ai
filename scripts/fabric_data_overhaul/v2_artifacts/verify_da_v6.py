"""Verify the deployed v6 aiInstructions contain expected markers."""
import subprocess, requests, time, json, base64

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
            sz = len(base64.b64decode(p["payload"]))
            path = p["path"]
            print(f"  - {path} ({sz:,} bytes)")

        for p in parts:
            if p["path"] == "Files/Config/published/stage_config.json":
                content = base64.b64decode(p["payload"]).decode("utf-8")
                cfg = json.loads(content)
                ai = cfg.get("aiInstructions", "")
                print(f"  PUBLISHED aiInstructions: {len(ai):,} chars")
                markers = [
                    "§A. 値マッピング表",
                    "§B. 時系列分析テンプレート",
                    "§C. 派生指標の SQL",
                    "§D. 失敗復旧",
                    "destination_region = 'ハワイ'",
                    "HAVING COUNT(*) >= 30",
                    "RepeatCustomerRate",
                    "9.6",
                ]
                for m in markers:
                    found = "+" if m in ai else "-"
                    print(f"    {found} {m}")
            if p["path"] == "Files/Config/draft/stage_config.json":
                content = base64.b64decode(p["payload"]).decode("utf-8")
                cfg = json.loads(content)
                ai = cfg.get("aiInstructions", "")
                print(f"  DRAFT aiInstructions: {len(ai):,} chars")
        break
