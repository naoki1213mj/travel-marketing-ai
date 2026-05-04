"""Add lh_travel_marketing_v2 lakehouse as a second data source to Travel_Ontology_DA_v2.

Verified structure (from microsoft/unified-data-foundation-with-fabric-solution-accelerator
sample, 2026-05-04):
  - elements is NESTED: schema -> table -> column
  - each element has a fresh UUID `id`
  - column elements require `data_type` (varchar, int, datetime2, ...)
  - schema may have is_selected=False while its tables are is_selected=True
  - description fields are null (not "")

Columns are discovered at script-time from INFORMATION_SCHEMA on the v2 SQL endpoint so
the part stays in sync with the actual lakehouse schema.

This first patch is STRUCTURAL ONLY: dataSourceInstructions describes the lakehouse and
output formatting but does NOT contain routing policy ("use lakehouse for time-series" etc.)
to keep blast radius small. Routing guidance moves into stage_config.aiInstructions
in a separate follow-up patch only after smoke confirms the new datasource is non-regressing.

Usage:
    uv run python scripts/fabric_data_overhaul/v2_artifacts/patch_add_lakehouse.py [--dry-run]

Environment:
    FABRIC_SQL_ENDPOINT  override for the SQL endpoint host. Defaults to the v2 endpoint.

Idempotent: re-runs that find both lakehouse parts already present exit 0 with no push.
Pre-flight backup is dumped to backups/agent_definition_pre_lakehouse_<ts>.json on every run.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import struct
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Reuse helpers from patch_demo_few_shot for token / fetch_live_definition / push_definition
sys.path.insert(0, str(Path(__file__).resolve().parent))
import patch_demo_few_shot as helpers  # type: ignore  # noqa: E402

ARTIFACTS_DIR = Path(__file__).resolve().parent
BACKUP_DIR = ARTIFACTS_DIR / "backups"

LAKEHOUSE_NAME = "lh_travel_marketing_v2"
LAKEHOUSE_ID = "5e02348e-d2a4-47fb-b63d-257ed3be7731"
LAKEHOUSE_SCHEMA = "dbo"
WORKSPACE_ID = helpers.WS_ID  # 096ff72a-6174-4aba-8f0c-140454fa6c3f

DEFAULT_SQL_ENDPOINT = (
    "pabkxzbptdhuzf2qxkx52ftsp4-fl3w6clumg5evdymcqcfj6tmh4.datawarehouse.fabric.microsoft.com"
)
SQL_TOKEN_SCOPE = "https://database.windows.net/.default"

# Microsoft Learn convention: directory uses hyphen `lakehouse-tables-{name}` even though
# the datasource.json `type` field is underscore form `lakehouse_tables`.
DRAFT_PART_PATH = f"Files/Config/draft/lakehouse-tables-{LAKEHOUSE_NAME}/datasource.json"
PUBLISHED_PART_PATH = f"Files/Config/published/lakehouse-tables-{LAKEHOUSE_NAME}/datasource.json"

# 10 Delta tables we expect under dbo. If SQL discovery returns a strict subset of these
# we still continue (fewer tables = OK) but if more are returned we keep all of them.
EXPECTED_TABLES = {
    "customer", "booking", "payment", "itinerary_item", "hotel",
    "flight", "tour_review", "campaign", "inquiry", "cancellation",
}

# Structural-only dataSourceInstructions. NO routing policy; only schema description and
# output format guidance to avoid silently changing NL2 router behavior.
LAKEHOUSE_INSTRUCTIONS = """lh_travel_marketing_v2 は dbo schema 配下に Delta テーブルとして travel marketing 運用データを保持する Microsoft Fabric Lakehouse です。

## 主要テーブル概要 (実 schema 抜粋)
- customer: 顧客マスタ。customer_id, age_band, gender, customer_segment, loyalty_tier, acquisition_channel, prefecture, email_opt_in
- booking: 予約。booking_id, customer_id, campaign_id, plan_name, product_type, destination_country, destination_region, destination_city, destination_type, season, departure_date, return_date, duration_days, pax, pax_adult, pax_child, total_revenue_jpy, price_per_person_jpy, booking_date, lead_time_days, booking_status
- payment: 決済。payment_id, booking_id, payment_method, payment_status, amount_jpy, currency, paid_at, installment_count
- itinerary_item: 行程アイテム。itinerary_item_id, booking_id, item_type, item_name, hotel_id, flight_id, start_date, end_date, nights, unit_price_jpy, quantity, total_price_jpy
- hotel: ホテルマスタ。hotel_id, name, country, region, city, category, star_rating, room_count, avg_price_per_night_jpy
- flight: 航空便。flight_id, airline_code, airline_name, departure_airport, arrival_airport, route_label, flight_class, distance_km
- tour_review: ツアーレビュー。review_id, booking_id, customer_id, plan_name, destination_region, rating, nps, comment, sentiment, review_date
- campaign: キャンペーン。campaign_id, campaign_name, campaign_type, target_segment, target_destination_type, start_date, end_date, discount_percent, total_budget_jpy, total_redemptions
- inquiry: 問合せ。inquiry_id, customer_id, channel, inquiry_type, subject, body, received_at, resolved_at, resolution_minutes, csat
- cancellation: cancellation_id, booking_id, cancelled_at, cancellation_reason, cancellation_lead_days, cancellation_fee_jpy, refund_amount_jpy
"""


def discover_columns_via_sql(allow_drift: bool) -> dict[str, list[tuple[str, str]]]:
    """Return {table_name: [(column_name, data_type), ...]} for tables in dbo schema.

    Uses pyodbc + Azure AD access token (DefaultAzureCredential).
    Fails closed when the discovered table set differs from EXPECTED_TABLES unless
    allow_drift=True is passed (--allow-schema-drift CLI flag).
    """
    try:
        import pyodbc
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(f"missing dependency: {exc}. run `uv sync` first.") from exc

    endpoint = os.environ.get("FABRIC_SQL_ENDPOINT") or DEFAULT_SQL_ENDPOINT
    database = LAKEHOUSE_NAME

    print(f"Discovering columns via SQL endpoint {endpoint}/{database}...")
    credential = DefaultAzureCredential(exclude_interactive_browser_credential=False)
    token = credential.get_token(SQL_TOKEN_SCOPE)
    token_bytes = token.token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)

    conn = pyodbc.connect(
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={endpoint};"
        f"Database={database};"
        f"Encrypt=yes;"
        f"TrustServerCertificate=no",
        attrs_before={1256: token_struct},
        timeout=15,
    )
    conn.timeout = 60

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, ORDINAL_POSITION
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = ?
        ORDER BY TABLE_NAME, ORDINAL_POSITION
        """,
        LAKEHOUSE_SCHEMA,
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    by_table: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        tbl, col, dtype, _ord = row
        by_table.setdefault(tbl, []).append((col, dtype))

    if not by_table:
        raise SystemExit(
            f"INFORMATION_SCHEMA.COLUMNS returned 0 rows for schema {LAKEHOUSE_SCHEMA!r}. "
            f"Verify SQL endpoint host and that the caller has access to {LAKEHOUSE_NAME}."
        )

    discovered = set(by_table)
    missing = EXPECTED_TABLES - discovered
    extra = discovered - EXPECTED_TABLES
    if missing or extra:
        if allow_drift:
            if missing:
                print(f"  warning (drift allowed): expected tables not found: {sorted(missing)}")
            if extra:
                print(f"  info (drift allowed): extra tables included: {sorted(extra)}")
        else:
            err = ["Schema drift detected vs EXPECTED_TABLES — refusing to push."]
            if missing:
                err.append(f"  missing: {sorted(missing)}")
            if extra:
                err.append(f"  extra:   {sorted(extra)}")
            err.append("Re-run with --allow-schema-drift to override (production: investigate first).")
            raise SystemExit("\n".join(err))

    print(f"  discovered {len(by_table)} tables / {sum(len(c) for c in by_table.values())} columns")
    return by_table


def build_lakehouse_datasource_payload(columns_by_table: dict[str, list[tuple[str, str]]]) -> dict:
    """Construct datasource.json content using nested schema -> table -> column elements."""
    table_elements = []
    for tname in sorted(columns_by_table):
        cols = columns_by_table[tname]
        column_elements = [
            {
                "id": str(uuid.uuid4()),
                "is_selected": True,
                "display_name": cname,
                "type": "lakehouse_tables.column",
                "data_type": dtype,
                "description": None,
                "children": [],
            }
            for cname, dtype in cols
        ]
        table_elements.append({
            "id": str(uuid.uuid4()),
            "is_selected": True,
            "display_name": tname,
            "type": "lakehouse_tables.table",
            "description": None,
            "children": column_elements,
        })

    schema_element = {
        "id": str(uuid.uuid4()),
        "is_selected": False,
        "display_name": LAKEHOUSE_SCHEMA,
        "type": "lakehouse_tables.schema",
        "description": None,
        "children": table_elements,
    }

    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/dataAgent/definition/dataSource/1.0.0/schema.json",
        "artifactId": LAKEHOUSE_ID,
        "workspaceId": WORKSPACE_ID,
        "dataSourceInstructions": LAKEHOUSE_INSTRUCTIONS,
        "displayName": LAKEHOUSE_NAME,
        "type": "lakehouse_tables",
        "userDescription": "Travel marketing lakehouse with booking, customer, review, payment and operational tables.",
        "metadata": {},
        "elements": [schema_element],
    }


def build_part(path: str, payload: dict) -> dict:
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")).decode("ascii")
    return {
        "path": path,
        "payload": encoded,
        "payloadType": "InlineBase64",
    }


def add_lakehouse_parts(definition: dict, columns_by_table: dict[str, list[tuple[str, str]]]) -> tuple[dict, bool]:
    """Append draft + published lakehouse parts. Returns (definition, changed)."""
    parts = definition["definition"]["parts"]
    have_draft = any(p["path"] == DRAFT_PART_PATH for p in parts)
    have_published = any(p["path"] == PUBLISHED_PART_PATH for p in parts)
    if have_draft and have_published:
        return definition, False

    payload = build_lakehouse_datasource_payload(columns_by_table)
    if not have_draft:
        parts.append(build_part(DRAFT_PART_PATH, payload))
        print(f"  + appended {DRAFT_PART_PATH}")
    if not have_published:
        parts.append(build_part(PUBLISHED_PART_PATH, payload))
        print(f"  + appended {PUBLISHED_PART_PATH}")
    return definition, True


def verify_post_push(expected_table_count: int) -> None:
    """Re-fetch live definition and assert the lakehouse parts are as intended."""
    print("Re-fetching live definition for read-back verification...")
    live = helpers.fetch_live_definition()
    parts = live.get("definition", {}).get("parts", [])
    draft = next((p for p in parts if p["path"] == DRAFT_PART_PATH), None)
    published = next((p for p in parts if p["path"] == PUBLISHED_PART_PATH), None)
    if draft is None or published is None:
        raise SystemExit(
            f"❌ read-back failed: missing parts (draft={'✅' if draft else '❌'} published={'✅' if published else '❌'})"
        )
    decoded = json.loads(base64.b64decode(draft["payload"]).decode("utf-8"))
    if decoded.get("artifactId") != LAKEHOUSE_ID:
        raise SystemExit(f"❌ read-back artifactId mismatch: got {decoded.get('artifactId')!r}, want {LAKEHOUSE_ID!r}")
    if decoded.get("workspaceId") != WORKSPACE_ID:
        raise SystemExit(f"❌ read-back workspaceId mismatch: got {decoded.get('workspaceId')!r}, want {WORKSPACE_ID!r}")
    if decoded.get("type") != "lakehouse_tables":
        raise SystemExit(f"❌ read-back type mismatch: got {decoded.get('type')!r}, want 'lakehouse_tables'")
    elements = decoded.get("elements", [])
    if not elements or elements[0].get("type") != "lakehouse_tables.schema":
        raise SystemExit("❌ read-back schema element missing")
    schema_children = elements[0].get("children", [])
    if len(schema_children) != expected_table_count:
        raise SystemExit(
            f"❌ read-back table count mismatch: got {len(schema_children)}, want {expected_table_count}"
        )
    print(f"  ✅ read-back verified: artifactId={LAKEHOUSE_ID}, workspaceId={WORKSPACE_ID}, tables={len(schema_children)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Build payload + diff only, do not push")
    parser.add_argument("--columns-only", action="store_true", help="Just print discovered columns and exit")
    parser.add_argument(
        "--allow-schema-drift",
        action="store_true",
        help="Permit discovered table set to differ from EXPECTED_TABLES (off by default for prod safety)",
    )
    args = parser.parse_args()

    columns_by_table = discover_columns_via_sql(allow_drift=args.allow_schema_drift)
    if args.columns_only:
        for tname in sorted(columns_by_table):
            cols = columns_by_table[tname]
            print(f"  {tname}: {len(cols)} columns")
            for cname, dtype in cols:
                print(f"    {cname}: {dtype}")
        return 0

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("Fetching live definition...")
    definition = helpers.fetch_live_definition()
    pre_size = sum(len(p.get("payload", "")) for p in definition["definition"]["parts"])
    print(f"  live parts={len(definition['definition']['parts'])} pre_payload_size={pre_size}")

    backup_path = BACKUP_DIR / f"agent_definition_pre_lakehouse_{timestamp}.json"
    backup_path.write_text(json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  backup saved to {backup_path.name}")

    new_definition, changed = add_lakehouse_parts(definition, columns_by_table)
    if not changed:
        print("Lakehouse parts already present in live definition — no-op success")
        return 0

    if args.dry_run:
        diff_path = ARTIFACTS_DIR / f"lakehouse_patch_dryrun_{timestamp}.json"
        diff_path.write_text(json.dumps(new_definition, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"DRY RUN: payload written to {diff_path.name}, NOT pushed")
        return 0

    print("Pushing updateDefinition LRO...")
    helpers.push_definition(new_definition)
    print("✅ updateDefinition succeeded")

    verify_post_push(expected_table_count=len(columns_by_table))

    print("\n" + "=" * 70)
    print("✅ Lakehouse parts added to Travel_Ontology_DA_v2")
    print("=" * 70)
    print("\nNext steps (RUN IMMEDIATELY):")
    print("  1. Smoke 4 demo prompts:")
    print("     uv run python scripts/fabric_data_overhaul/v2_artifacts/smoke_demo_prompts.py")
    print("  2. 14-prompt regression vs Phase 9.6 12/14 grade A baseline:")
    print("     uv run python scripts/fabric_data_overhaul/v2_artifacts/smoke_test_v6.py")
    print("\nIf regression detected, rollback with:")
    print(f"  uv run python scripts/fabric_data_overhaul/v2_artifacts/rollback_to_backup.py {backup_path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
