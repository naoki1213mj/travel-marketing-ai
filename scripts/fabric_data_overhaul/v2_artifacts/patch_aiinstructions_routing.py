"""Phase 11a: Update stage_config.aiInstructions to add explicit Lakehouse routing rules.

Adds two sections per Microsoft Fabric Data Agent best-practice §8 (data agent instructions)
and rubber-duck v3 non-blocking #1:
- "## Data sources" expanded to include Lakehouse `lh_travel_marketing_v2` as a 1st-class
  routable datasource (not just as ontology backing storage)
- "## When asked about" added as the new last section, with explicit routing decisions:
  ontology-first for entity/relationship reasoning, lakehouse-second for direct tabular
  aggregations, fallback rules when one path fails

This patch is text-only and additive (no part add/remove, no datasource.json change).
LRO is atomic — validation error keeps prior 2463-char aiInstructions intact.

Idempotent: looks for the marker `## When asked about` in the live aiInstructions; if
found, no-op success.

Usage:
    uv run python scripts/fabric_data_overhaul/v2_artifacts/patch_aiinstructions_routing.py [--dry-run]

Rollback:
    uv run python scripts/fabric_data_overhaul/v2_artifacts/rollback_to_backup.py \
        agent_definition_pre_aiinstructions_routing_<timestamp>.json
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ARTIFACTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ARTIFACTS_DIR))
import patch_demo_few_shot as helpers  # noqa: E402

BACKUP_DIR = ARTIFACTS_DIR / "backups"

DRAFT_STAGE_PATH = "Files/Config/draft/stage_config.json"
PUBLISHED_STAGE_PATH = "Files/Config/published/stage_config.json"

ROUTING_MARKER = "## When asked about"

DATA_SOURCES_SECTION_NEW = """## Data sources
Travel_Ontology_DA_v2 には 2 つのデータソースが登録されています。

1. **`travelIQ_v2` ontology** — 概念 / relationship reasoning + KPI 定義の正解
   - 元の Lakehouse: `lh_travel_marketing_v2`、schema `dbo`
   - 利用可能 entity (10):
     customer / booking / payment / cancellation / itinerary_item / hotel / flight / tour_review / campaign / inquiry
   - 各 entity の列マッピング・値正規化ルール (沖縄/Okinawa/oki 揺れ等) ・テンプレ SQL・GQL examples・KPI のビジネス制約 (`booking_status IN ('confirmed','completed')`, `HAVING >= 30` 等) は **ontology side の `dataSourceInstructions` §A〜§G** にすべて記載。集計や KPI で迷ったらここに従う (lakehouse 側で独自に再構築しない)。
   - リレーション横断 / 概念的グルーピング / セマンティック比較 / 期間 KPI 集計に最適。

2. **`lh_travel_marketing_v2` lakehouse** — 直接 T-SQL アクセス (限定用途)
   - 同じ 10 テーブルへの T-SQL クエリ (Fabric SQL endpoint, T-SQL 方言: `TOP (N)` / `OFFSET ... FETCH NEXT N ROWS ONLY`、`LIMIT` は使えない)。
   - 用途は ontology が苦手なシナリオに限定: 個票照会 (`customer_id` / `booking_id` / `payment_id` の詳細レコード)、特定テーブルから最新 N 件のヘッドライン取得、ontology が 0 件返したときの `SELECT COUNT(*)` 実在性検証。
   - **KPI / 期間集計 / リピート率 / 平均単価 / キャンペーン ROI 等は lakehouse 直叩きしない** (ontology の §C テンプレを使う)。lakehouse はビジネス制約のないスキーマ素のテーブルなので、`booking_status` フィルタや HAVING 条件が抜けると数値が誤る。

外部データ (天気 / 観光庁統計 / 競合社情報 / 為替 API 等) を取得することは **禁止**。"""

ROUTING_SECTION_APPEND = """## When asked about

### 質問パターンと推奨データソース

KPI / 集計 / セマンティック推論は **ontology を最優先**。lakehouse は raw row 取得や個票照会など ontology が苦手なシナリオに限定する。

- **「○○の人気プラン」「ランキング」「シーズン×地域×セグメントの売上 Top N」** → **ontology** を最初に使い、`booking` / `customer` / `tour_review` 等の entity を JOIN して GROUP BY 集計する。
- **「合計売上 / 件数 / 月次推移 / 期間 KPI」「リピート率 / キャンセル率 / NPS / 平均単価」「キャンペーン ROI / 為替影響」** → **ontology** の `dataSourceInstructions §B / §C / §E / §F / §G` の値正規化・テンプレ SQL・GQL examples を必ず使う。lakehouse 直叩きで KPI 計算を勝手に再構築しない (`booking_status IN ('confirmed','completed')`、`HAVING >= 30` 等のビジネス制約が抜けて数値が誤る恐れがある)。
- **「リレーション横断 (顧客 → 予約 → レビュー → 決済)」「セマンティック比較」「relationship-aware 検索」** → **ontology** の MATCH ... RETURN GQL クエリを使う。
- **「特定 customer_id / booking_id / payment_id の生レコード」「指定 ID の詳細フィールド一括」「特定テーブルから最新 N 件のヘッドライン取得」** → **lakehouse** から `SELECT TOP (N) * FROM dbo.<table> WHERE ... ORDER BY ... DESC` で fetch (T-SQL: `LIMIT` ではなく `TOP (N)` または `OFFSET ... FETCH NEXT N ROWS ONLY` を使うこと)。
- **「あるレコード / 値が DB に存在するかの存在確認」「ontology が 0 件のときの cross-check」** → **lakehouse** の `SELECT COUNT(*) FROM dbo.<table> WHERE ...` で実在検証。

### フォールバック判断 (CRITICAL)

「0 件」は完了ではなく未完了として扱う。grounded で正の数値が返ったときのみ完了とみなす。

1. **ontology が 0 件返した場合**: まず ontology の `dataSourceInstructions §B` 値正規化 (例: `沖縄` `Okinawa` `oki` 揺れ吸収) → DISTINCT 確認 → 部分一致再試行。それでも 0 なら **lakehouse 側で同条件の `SELECT COUNT(*) FROM dbo.<table> WHERE ...` で実在性のみ検証** する。実在が確認できたら ontology の §C テンプレに戻って relationship を緩めて再集計、実在しないなら「該当データなし」と回答可。
2. **lakehouse が 0 件返した場合 (ID/存在確認以外)**: そもそも KPI 集計を lakehouse に振っていれば設計上の誤り。ontology の §C テンプレ SQL に戻して relationship-aware に再集計する。
3. **両方 0 件の場合のみ**「該当データなし」と回答可。緩和した条件と試したパスを補足に明記する。

### 重要原則

- 1 つのデータソースで完結する質問は、もう一方のデータソースに余計に問い合わせない (latency 削減)。
- ただし結果が 0 件 / 不完全なときは、必ずもう一方を試してから「データなし」と結論する (上記 §フォールバック判断 を参照)。
- KPI / 集計 / セマンティック推論で迷ったら ontology に倒す。lakehouse-first で速さを優先して KPI 定義を独自再構築しない。
- 内部のデータソース選択ロジック (NL2Ontology / NL2SQL / routing 判断) を最終回答に出さない。"""


def update_ai_instructions(current: str) -> str:
    """Apply Phase 11a routing-improvement edits to aiInstructions."""
    out = current

    # Replace the existing "## Data sources" section through to the next section start.
    ds_marker = "## Data sources"
    next_section_marker = "## Response guidelines"
    ds_start = out.find(ds_marker)
    if ds_start == -1:
        raise SystemExit(
            "Phase 11a expected '## Data sources' section in current aiInstructions; not found."
        )
    next_start = out.find(next_section_marker, ds_start)
    if next_start == -1:
        raise SystemExit(
            "Phase 11a expected '## Response guidelines' section after '## Data sources'; not found."
        )
    out = out[:ds_start] + DATA_SOURCES_SECTION_NEW + "\n\n" + out[next_start:]

    if ROUTING_MARKER in out:
        raise SystemExit(
            "Phase 11a marker already present after Data sources rewrite — aborting (unexpected state)."
        )

    if not out.endswith("\n"):
        out += "\n"
    out += "\n" + ROUTING_SECTION_APPEND
    return out


def patch_definition(definition: dict) -> tuple[dict, bool]:
    """Return (new_definition, changed)."""
    parts = definition.get("definition", {}).get("parts", [])
    target_paths = {DRAFT_STAGE_PATH, PUBLISHED_STAGE_PATH}
    found_paths = set()
    new_parts = []
    changed = False

    for part in parts:
        if part["path"] in target_paths:
            found_paths.add(part["path"])
            payload_text = base64.b64decode(part["payload"]).decode("utf-8")
            obj = json.loads(payload_text)
            current_ai = obj.get("aiInstructions", "")
            if ROUTING_MARKER in current_ai:
                print(f"  no-op: {part['path']} already has routing rules")
                new_parts.append(part)
                continue
            new_ai = update_ai_instructions(current_ai)
            obj["aiInstructions"] = new_ai
            new_payload = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
            new_part = {
                "path": part["path"],
                "payload": base64.b64encode(new_payload).decode("ascii"),
                "payloadType": part.get("payloadType", "InlineBase64"),
            }
            new_parts.append(new_part)
            changed = True
            print(f"  + updated {part['path']} (aiInstructions {len(current_ai)} → {len(new_ai)} chars)")
        else:
            new_parts.append(part)

    missing = target_paths - found_paths
    if missing:
        raise SystemExit(f"❌ live definition missing expected paths: {sorted(missing)}")

    new_definition = {"definition": {"parts": new_parts}}
    return new_definition, changed


def verify_post_push() -> None:
    """Re-fetch and assert routing rules are present in both draft and published."""
    print("Re-fetching live definition for read-back verification...")
    live = helpers.fetch_live_definition()
    parts = live.get("definition", {}).get("parts", [])
    for path in (DRAFT_STAGE_PATH, PUBLISHED_STAGE_PATH):
        part = next((p for p in parts if p["path"] == path), None)
        if part is None:
            raise SystemExit(f"❌ read-back failed: {path} missing")
        decoded = json.loads(base64.b64decode(part["payload"]).decode("utf-8"))
        ai = decoded.get("aiInstructions", "")
        if ROUTING_MARKER not in ai:
            raise SystemExit(f"❌ read-back failed: {path} missing '{ROUTING_MARKER}' marker")
        print(f"  ✅ {path}: {len(ai)} chars, marker present")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    print("Fetching live definition...")
    definition = helpers.fetch_live_definition()
    pre_size = sum(len(p.get("payload", "")) for p in definition["definition"]["parts"])
    print(f"  live parts={len(definition['definition']['parts'])} pre_payload_size={pre_size}")

    backup_path = BACKUP_DIR / f"agent_definition_pre_aiinstructions_routing_{timestamp}.json"
    backup_path.write_text(json.dumps(definition, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"  backup saved to {backup_path.name}")

    new_definition, changed = patch_definition(definition)
    if not changed:
        print("aiInstructions already has routing rules — no-op success")
        return 0

    if args.dry_run:
        diff_path = ARTIFACTS_DIR / f"aiinstructions_routing_dryrun_{timestamp}.json"
        diff_path.write_text(json.dumps(new_definition, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"DRY RUN: payload written to {diff_path.name}, NOT pushed")
        return 0

    print("Pushing updateDefinition LRO...")
    helpers.push_definition(new_definition)
    print("✅ updateDefinition succeeded")

    verify_post_push()

    print("\n" + "=" * 70)
    print("✅ Phase 11a aiInstructions routing rules pushed to Travel_Ontology_DA_v2")
    print("=" * 70)
    print("\nNext: smoke regression check (run 2-3x for variance averaging):")
    print("  uv run python scripts/fabric_data_overhaul/v2_artifacts/smoke_demo_prompts.py")
    print("\nIf regression detected, rollback with:")
    print(f"  uv run python scripts/fabric_data_overhaul/v2_artifacts/rollback_to_backup.py {backup_path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
