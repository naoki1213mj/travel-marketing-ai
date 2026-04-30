"""Poll a previously-submitted LRO."""
import sys, subprocess, requests, time, json

OP = "https://wabi-west-us3-a-primary-redirect.analysis.windows.net/v1/operations/b8689221-c59f-4ed3-b2ff-f83fd16e8e1d"
F = "https://api.fabric.microsoft.com"

t = subprocess.run(
    ["az", "account", "get-access-token", "--resource", F, "--query", "accessToken", "-o", "tsv"],
    capture_output=True, text=True, shell=True, check=True
).stdout.strip()
h = {"Authorization": f"Bearer {t}"}

sess = requests.Session()
sess.headers.update(h)
sess.mount("https://", requests.adapters.HTTPAdapter(max_retries=5))

for i in range(180):
    try:
        rr = sess.get(OP, timeout=30)
    except Exception as e:
        print(f"poll {i}: ERR {e}")
        time.sleep(5)
        continue
    if rr.status_code != 200:
        print(f"poll {i}: {rr.status_code} {rr.text[:200]}")
        time.sleep(3)
        continue
    st = rr.json().get("status")
    print(f"poll {i}: {st}")
    if st == "Succeeded":
        try:
            rrr = sess.get(OP + "/result", timeout=30)
            print("Result:", rrr.text[:1000])
        except Exception:
            pass
        sys.exit(0)
    if st in ("Failed", "Canceled"):
        print("FAILED/CANCELED:")
        print(json.dumps(rr.json(), ensure_ascii=False, indent=2))
        sys.exit(2)
    time.sleep(2)
print("TIMEOUT")
