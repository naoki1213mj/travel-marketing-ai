"""Patch Travel_Ontology_DA_v2 datasource instructions to add §F GQL Examples
and §G GQL anti-patterns, validated by probe_gql_hint.py.

This addresses the user's report 2026-05-02 that 'Fabric Data Agent ちゃんと
使えるようにしたい' — the structured retry shipped in commit 062a1ea wasn't
enough because the Fabric Data Agent itself emitted invalid GQL (booking_id
projection mixed with SUM/COUNT aggregates without GROUP BY) and gave up.

Validated via probe_gql_hint.py 2026-05-02:
- baseline (no hint): completed but with booking_id leak in nl2code variation 1
- with-gql-hint: completed, NO booking_id leak, returned ¥3,286,928,770 / 3,013件
- with-gql-hint on user's prompt 「夏のハワイ学生旅行向けプランを企画して」:
  status=completed, low_conf=False, no booking_id leak

Steps:
1. Backup current agent_definition_tuned_v2.json to backups/
2. Decode draft + published datasource.json
3. Insert ## §F + ## §G blocks BEFORE the existing dataSourceInstructions ends
4. Re-encode and POST updateDefinition LRO
5. Poll LRO until Succeeded
6. Save patched JSON to agent_definition_tuned_v2_with_gql_examples.json

Phase 10 baseline preservation:
- §F is ADDITIVE (only adds GQL patterns where previously only SQL was given)
- §G is purely negative constraints prohibiting known-bad patterns (booking_id
  in aggregate, LOWER() wrapper, etc.)
- size delta: +~2KB (16135 → ~18KB), well within Fabric's instruction limits

post-deploy verification:
  uv run python scripts/fabric_data_overhaul/v2_artifacts/probe_live_da.py
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

WS_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
ARTIFACTS_DIR = Path(__file__).resolve().parent
DEF_PATH = ARTIFACTS_DIR / "agent_definition_tuned_v2.json"
BACKUP_DIR = ARTIFACTS_DIR / "backups"
BACKUP_DIR.mkdir(exist_ok=True)

GQL_F_AND_G_BLOCK = """

## §F. GQL 出力テンプレート (analyze_ontology / NL2Ontology が GQL を選んだとき必ずこの形に従う)

Fabric Data Agent は SQL に切り替えられないクエリ (たとえば短い「ハワイの売上を教えて」型)
で内部的に NL2Ontology → GQL を生成する。SQL テンプレ (§E) があっても GQL 経路では
別ルールが必要なため、ここに明示する。

### F.1 単一条件サマリ — `MATCH ... RETURN SUM/COUNT/AVG` のみ
```
MATCH (b:booking)
WHERE b.destination_region = "ハワイ"
  AND b.booking_status IN ["confirmed", "completed"]
RETURN SUM(b.total_revenue_jpy) AS revenue_jpy,
       COUNT(b) AS bookings,
       SUM(b.pax) AS travelers
```
**RETURN 句に `b.booking_id` / `b.customer_id` / 表示列を入れない**
(scalar + aggregate 混在 → invalid GQL → server_error)。

### F.2 booking + customer JOIN — segment / age 絞り込み
ontology 上の関係名は `booking_has_customer` (CONFIRMED via ontology audit)。
学生 / シニア / ファミリーで絞るときはこれを使う:

```
MATCH (b:booking)-[:booking_has_customer]->(c:customer)
WHERE b.destination_region = "ハワイ"
  AND b.season = "summer"
  AND c.customer_segment = "student"
  AND b.booking_status IN ["confirmed", "completed"]
RETURN SUM(b.total_revenue_jpy) AS revenue_jpy,
       COUNT(b) AS bookings,
       SUM(b.pax) AS travelers,
       AVG(b.price_per_person_jpy) AS avg_price_jpy
```

### F.3 ranking — GROUP BY を必ず入れる
```
MATCH (b:booking)
WHERE b.booking_status IN ["confirmed", "completed"]
RETURN b.destination_region AS region,
       SUM(b.total_revenue_jpy) AS revenue_jpy,
       COUNT(b) AS bookings,
       SUM(b.pax) AS travelers
ORDER BY revenue_jpy DESC LIMIT 10
```

## §G. GQL anti-patterns (絶対禁止 — どれを書いても server_error の原因になる)

- ❌ **aggregate を伴う RETURN に booking_id / customer_id / 表示列を含める**
  → "All variations failed" の原因 #1。集計のみ返すか、GROUP BY で展開する。
- ❌ **`LOWER(b.destination_region) = LOWER("ハワイ")` のような大小比較ラッパー**
  → 値マッピング表で正規化済の文字列をそのまま `=` 比較する。LOWER() は不要かつ
  index 利用を阻害する。
- ❌ **`b.booking_status = "confirmed"` (単一値)** ではなく
  ✅ **`b.booking_status IN ["confirmed", "completed"]`** を使う
  (キャンセル除外で売上の二重カウントを防ぐ慣習)。
- ❌ **destination_region 値を **英語化しない**: 値マッピング表に従い `"ハワイ" / "沖縄" /
  "京都"` などはそのまま日本語で WHERE する (英語化は country 列のみ)。
- ✅ customer_segment / season / age_band は英語小文字 (`"student" / "summer" / "20s"`)
  を使う。日本語ユーザー入力は値マッピング表で英語に変換済の前提。
"""


def get_token() -> str:
    """Fabric API token (audience: api.fabric.microsoft.com)."""
    r = subprocess.run(
        [
            "az", "account", "get-access-token",
            "--resource", "https://api.fabric.microsoft.com",
            "--query", "accessToken", "-o", "tsv",
        ],
        capture_output=True, text=True, shell=True, check=True,
    )
    return r.stdout.strip()


def fetch_live_definition() -> dict:
    """Fetch the current live Data Agent definition via getDefinition LRO."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS_ID}/dataAgents/{DA_ID}/getDefinition"
    )
    print(f"POST {url}")
    r = requests.post(url, headers=headers, json={}, timeout=60)
    r.raise_for_status()
    op_id = r.headers.get("x-ms-operation-id") or r.headers.get("Location", "").rsplit("/", 1)[-1]
    print(f"  LRO {op_id}, polling...")
    for i in range(30):
        time.sleep(3)
        op = requests.get(
            f"https://api.fabric.microsoft.com/v1/operations/{op_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        op.raise_for_status()
        status = op.json().get("status")
        print(f"  poll {i + 1} status={status}")
        if status == "Succeeded":
            break
        if status == "Failed":
            raise RuntimeError(f"getDefinition LRO failed: {op.text}")
    result = requests.get(
        f"https://api.fabric.microsoft.com/v1/operations/{op_id}/result",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    result.raise_for_status()
    return result.json()


def patch_datasource_instructions(definition: dict) -> dict:
    """Append §F GQL examples + §G anti-patterns to BOTH draft and published datasource.json."""
    parts = definition["definition"]["parts"]
    patched_count = 0
    for p in parts:
        if "ontology-travelIQ_v2/datasource.json" not in p["path"]:
            continue
        decoded = base64.b64decode(p["payload"]).decode("utf-8")
        ds = json.loads(decoded)
        instructions = ds.get("dataSourceInstructions", "")
        if "## §F. GQL 出力テンプレート" in instructions:
            print(f"  {p['path']}: §F already present, skip")
            continue
        new_instructions = instructions.rstrip() + GQL_F_AND_G_BLOCK
        ds["dataSourceInstructions"] = new_instructions
        new_decoded = json.dumps(ds, ensure_ascii=False, indent=2)
        p["payload"] = base64.b64encode(new_decoded.encode("utf-8")).decode("ascii")
        patched_count += 1
        print(f"  {p['path']}: instructions {len(instructions)} → {len(new_instructions)} chars")
    if patched_count == 0:
        raise RuntimeError("No datasource.json parts found / all already patched")
    return definition


def push_definition(definition: dict) -> None:
    """POST updateDefinition LRO and poll for Succeeded."""
    token = get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Strip .platform per existing pattern (deploy_ontology_patch.py § doc)
    parts = [p for p in definition["definition"]["parts"] if p["path"] != ".platform"]
    body = {"definition": {"parts": parts}}
    url = (
        f"https://api.fabric.microsoft.com/v1/workspaces/{WS_ID}/dataAgents/{DA_ID}/updateDefinition"
    )
    print(f"POST {url} (parts={len(parts)})")
    r = requests.post(url, headers=headers, json=body, timeout=120)
    if r.status_code not in (200, 202):
        raise RuntimeError(f"updateDefinition failed: {r.status_code} {r.text}")
    op_id = r.headers.get("x-ms-operation-id") or r.headers.get("Location", "").rsplit("/", 1)[-1]
    if not op_id:
        print(f"  immediate response: {r.status_code} {r.text[:200]}")
        return
    print(f"  LRO {op_id}, polling...")
    for i in range(60):
        time.sleep(3)
        op = requests.get(
            f"https://api.fabric.microsoft.com/v1/operations/{op_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        op.raise_for_status()
        status = op.json().get("status")
        print(f"  poll {i + 1} status={status}")
        if status == "Succeeded":
            print("  ✅ updateDefinition Succeeded")
            return
        if status == "Failed":
            raise RuntimeError(f"updateDefinition LRO failed: {op.text}")
    raise TimeoutError("updateDefinition LRO timed out")


def main() -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = BACKUP_DIR / f"agent_definition_pre_gql_examples_{timestamp}.json"

    # 1. Backup current local artifact
    if DEF_PATH.exists():
        shutil.copy(DEF_PATH, backup_path)
        print(f"backup → {backup_path}")

    # 2. Fetch live definition (source of truth)
    print("\n=== fetch live definition ===")
    live = fetch_live_definition()

    # 3. Patch dataSourceInstructions
    print("\n=== patch §F + §G ===")
    patched = patch_datasource_instructions(live)

    # 4. Push back
    print("\n=== push updateDefinition ===")
    push_definition(patched)

    # 5. Save patched version locally
    out_path = ARTIFACTS_DIR / "agent_definition_tuned_v2_with_gql_examples.json"
    out_path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nSaved patched definition → {out_path}")
    print("\n✅ Done. Verify with:")
    print("  uv run python scripts/fabric_data_overhaul/v2_artifacts/probe_live_da.py")


if __name__ == "__main__":
    sys.exit(main())
