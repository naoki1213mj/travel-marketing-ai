"""Phase 11b: Append T-SQL example queries to lakehouse dataSourceInstructions.

Adds a section of lakehouse-direct T-SQL examples (ID 個票照会 / 最新 N 件 /
existence check / pagination) to `lh_travel_marketing_v2` datasource.

Rubber-duck (phase11b-plan) HOLD verdict applied:
- Removed ontology-routing policy block (centralized in stage_config aiInstructions).
- Removed aggressive "0件→ontology retry" rule.
- Example 2: ORDER BY booking_date DESC (matches NL "最新").
- Example 1: explicit columns (no SELECT *).
- Example 5: OFFSET/FETCH pagination (teaches ORDER BY requirement).
- Added 1-liner pointing back to stage_config for routing.

Idempotent via marker. Saves backup before push.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import patch_demo_few_shot as helpers  # noqa: E402

LAKEHOUSE_PATH_FRAGMENT = "lakehouse-tables-lh_travel_marketing_v2/datasource.json"
MARKER = "## T-SQL example queries (lakehouse-direct, limited use cases)"

EXAMPLES_SECTION = """

## T-SQL example queries (lakehouse-direct, limited use cases)

下記は lakehouse 直接 T-SQL 経路で使う典型例です (ID 個票照会 / 最新 N 件 /
existence check / pagination)。ルーティング判断は stage_config の aiInstructions
に従い、KPI 集計や前年比などのセマンティック質問は ontology 経由を選びます。
T-SQL syntax 必須 (LIMIT N は不可、TOP (N) または OFFSET ... FETCH NEXT N ROWS ONLY)。
TOP without ORDER BY は非決定的になるため必ず ORDER BY を付けます。
OFFSET ... FETCH NEXT には ORDER BY が必須です。

### Example 1: 予約 ID から個票取得
NL: 「予約 ID BK000123 の詳細を見せて」
SQL:
SELECT booking_id, booking_code, customer_id, campaign_id, plan_name, product_type,
       destination_country, destination_region, destination_city, destination_type,
       season, departure_date, return_date, duration_days, pax, pax_adult, pax_child,
       total_revenue_jpy, price_per_person_jpy, booking_date, lead_time_days,
       booking_status
FROM dbo.booking
WHERE booking_id = 'BK000123';

### Example 2: 最新 N 件の予約 (予約日順)
NL: 「最新 10 件の予約を見せて」
SQL:
SELECT TOP (10) booking_id, customer_id, plan_name, destination_region,
       booking_date, departure_date, total_revenue_jpy, booking_status
FROM dbo.booking
ORDER BY booking_date DESC;

### Example 3: 特定 booking の itinerary 詳細
NL: 「booking BK000123 の行程アイテム一覧」
SQL:
SELECT itinerary_item_id, item_type, item_name, hotel_id, flight_id,
       start_date, end_date, nights, unit_price_jpy, quantity, total_price_jpy
FROM dbo.itinerary_item
WHERE booking_id = 'BK000123'
ORDER BY start_date;

### Example 4: existence check (キャンペーン下に予約があるか)
NL: 「キャンペーン CMP001 で予約された booking が存在するか」
SQL:
SELECT TOP (1) booking_id
FROM dbo.booking
WHERE campaign_id = 'CMP001';

### Example 5: pagination (11〜20 件目の最新予約)
NL: 「最新の予約 11〜20 件目を見せて」
SQL:
SELECT booking_id, customer_id, plan_name, destination_region,
       booking_date, total_revenue_jpy, booking_status
FROM dbo.booking
ORDER BY booking_date DESC
OFFSET 10 ROWS FETCH NEXT 10 ROWS ONLY;

## 重要な原則 (lakehouse 直接 T-SQL 時)
個別行の照会・存在確認・直近 N 件のみに使い、ID/値が見つからない場合はキー正規化
や別カラムでの再確認を行います (ontology へのフォールバックはしない)。
"""


def patch_definition(definition: dict) -> tuple[dict, list[str]]:
    """Append example queries to both draft + published lakehouse datasource parts.

    Returns (patched_definition, list_of_paths_modified).
    """
    parts = definition["definition"]["parts"]
    modified: list[str] = []
    for p in parts:
        if LAKEHOUSE_PATH_FRAGMENT not in p["path"]:
            continue
        payload_text = base64.b64decode(p["payload"]).decode("utf-8")
        obj = json.loads(payload_text)
        current = obj.get("dataSourceInstructions", "") or ""
        if MARKER in current:
            print(f"  SKIP (idempotent, marker present): {p['path']}")
            continue
        new_instructions = current.rstrip() + EXAMPLES_SECTION
        obj["dataSourceInstructions"] = new_instructions
        new_payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        p["payload"] = base64.b64encode(new_payload.encode("utf-8")).decode("ascii")
        modified.append(p["path"])
        print(f"  PATCHED: {p['path']} -- dataSourceInstructions {len(current)} -> {len(new_instructions)} chars")
    return definition, modified


def verify_post_push() -> None:
    """Re-fetch live and assert marker present in both draft + published lakehouse parts."""
    print("Re-fetching live definition for verification...")
    live = helpers.fetch_live_definition()
    parts = live["definition"]["parts"]
    seen_paths = []
    for p in parts:
        if LAKEHOUSE_PATH_FRAGMENT not in p["path"]:
            continue
        payload_text = base64.b64decode(p["payload"]).decode("utf-8")
        obj = json.loads(payload_text)
        ins = obj.get("dataSourceInstructions", "") or ""
        if MARKER not in ins:
            raise SystemExit(f"VERIFICATION FAILED: marker missing in {p['path']}")
        seen_paths.append((p["path"], len(ins)))
        print(f"  OK: {p['path']} ({len(ins)} chars, marker present)")
    if len(seen_paths) < 2:
        raise SystemExit(f"VERIFICATION FAILED: expected 2 lakehouse parts (draft+published), saw {len(seen_paths)}")
    print("VERIFICATION OK")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="patch in memory only, no push")
    args = ap.parse_args()

    print("Fetching live definition...")
    definition = helpers.fetch_live_definition()
    parts_count = len(definition["definition"]["parts"])
    print(f"  parts: {parts_count}")

    if args.dry_run:
        print("\n=== DRY RUN ===")
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = HERE / f"lakehouse_examples_dryrun_{ts}.json"
        patched, modified = patch_definition(definition)
        out_path.write_text(json.dumps(patched, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  modified parts: {modified}")
        print(f"  dryrun saved: {out_path.name}")
        return 0

    print("\n=== LIVE PUSH ===")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = HERE / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"agent_definition_pre_lakehouse_examples_{ts}.json"
    backup_path.write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  backup: {backup_path.name}")

    patched, modified = patch_definition(definition)
    if not modified:
        print("  nothing to patch (idempotent no-op)")
        return 0

    print(f"  pushing patch (modified {len(modified)} parts)...")
    helpers.push_definition(patched)
    print("  push OK")

    verify_post_push()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
