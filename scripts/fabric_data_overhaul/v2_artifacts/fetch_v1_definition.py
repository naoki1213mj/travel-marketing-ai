"""Fetch v1 Travel_Ontology_DA definition to inspect lakehouse data source structure
(if any) and use it as a reference template for adding a lakehouse data source to v2.

v1 may have lakehouse data sources (travel_sales / travel_review on Travel_LH) which
gives us a real Fabric-validated example of `Files/Config/draft/lakehouse-tables-{name}/datasource.json`
shape — far more reliable than constructing it from schema docs alone.
"""
from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

import requests

WS_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
V1_DA_ID = "6726b401-0b63-4aeb-8fab-0c755446a99d"
ARTIFACTS_DIR = Path(__file__).resolve().parent
OUT_PATH = ARTIFACTS_DIR / "v1_agent_definition.json"
DECODED_DIR = ARTIFACTS_DIR / "v1_decoded"


def get_token() -> str:
    import subprocess
    r = subprocess.run(
        ["az", "account", "get-access-token", "--resource", "https://api.fabric.microsoft.com", "--query", "accessToken", "-o", "tsv"],
        capture_output=True, text=True, check=True, shell=True,
    )
    return r.stdout.strip()


def fetch_definition(da_id: str) -> dict:
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"https://api.fabric.microsoft.com/v1/workspaces/{WS_ID}/dataAgents/{da_id}/getDefinition"
    print(f"POST {url}")
    r = requests.post(url, headers=headers, json={}, timeout=60)
    r.raise_for_status()
    op_id = r.headers.get("x-ms-operation-id") or r.headers.get("Location", "").rsplit("/", 1)[-1]
    print(f"  LRO {op_id}, polling...")
    for i in range(30):
        time.sleep(5)
        op = requests.get(
            f"https://api.fabric.microsoft.com/v1/operations/{op_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        op.raise_for_status()
        status = op.json().get("status")
        print(f"  poll {i + 1} status={status}")
        if status == "Succeeded":
            break
        if status == "Failed":
            raise RuntimeError(f"getDefinition failed: {op.text}")
    result = requests.get(
        f"https://api.fabric.microsoft.com/v1/operations/{op_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    result.raise_for_status()
    return result.json()


def main() -> int:
    print(f"Fetching v1 DA {V1_DA_ID} definition...")
    defn = fetch_definition(V1_DA_ID)
    OUT_PATH.write_text(json.dumps(defn, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved raw to {OUT_PATH}")

    DECODED_DIR.mkdir(exist_ok=True)
    parts = defn.get("definition", {}).get("parts", [])
    print(f"\n{len(parts)} parts:")
    for p in parts:
        path = p["path"]
        decoded = ""
        if "payload" in p:
            try:
                decoded = base64.b64decode(p["payload"]).decode("utf-8")
            except Exception:  # noqa: BLE001
                decoded = "<binary>"
        print(f"  {path} ({len(decoded)} chars)")
        if decoded and decoded != "<binary>":
            safe_name = path.replace("/", "__").replace(".platform", "platform")
            (DECODED_DIR / safe_name).write_text(decoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
