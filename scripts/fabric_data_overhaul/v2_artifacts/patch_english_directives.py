"""§D / §F / §G の英語化 patch script (live Fabric DA への in-place 編集)。

Travel_Ontology_DA_v2 の dataSourceInstructions のうち、構造的指示
(failure recovery checklist / GQL templates / GQL anti-patterns) を英語に
翻訳する。値マッピング表 (§A) / SQL テンプレ (§B/§C/§E) / §E.demo Few-Shot
(日本語デモプロンプト) は touch しない。

設計原則:
- 旅行データの実値 ('ハワイ' / '沖縄' / '京都' / 'spring' / 'student' 等)
  は WHERE 一致のためそのまま日本語/英語コードを保持する
- 構造的 prose (条件分岐 / retry ルール / disclaimer) は英語にする
- 出力指示は最終回答が日本語であるべきと明示する
- idempotent: version marker (`<!-- §X-en v1 -->`) で再実行時 no-op

API path:
1. Fetch live definition via getDefinition LRO
2. Save pre-patch backup to backups/agent_definition_pre_english_directives_*.json
3. For each of §D / §F / §G in BOTH draft and published:
   - Locate `## §D` ... next `## §E` (end anchor) — replace with English §D
   - Locate `## §F` ... next `## §G` (end anchor) — replace with English §F
   - Locate `## §G` ... EOS — replace with English §G
4. Push via updateDefinition LRO (90s poll budget + transient retry)

Rollback: backups/agent_definition_pre_english_directives_*.json を
updateDefinition LRO で再注入。
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, "scripts/fabric_data_overhaul/v2_artifacts")

from pathlib import Path

from patch_demo_few_shot import (  # noqa: E402
    DA_ID,
    WS_ID,
    fetch_live_definition,
    push_definition,
)

# 英語化された各セクション本文。テスト時は version marker を bump する。
EN_VERSION = "v1"

EN_SECTION_D = f"""## §D. Failure Recovery Checklist (CRITICAL — MUST execute before returning "no data") <!-- §D-en {EN_VERSION} -->

### D.1 Value normalization
- BEFORE building any WHERE clause, look up the user-provided term in the value mapping table at the top of these instructions.
- Example: "Hawaii" → `destination_region='ハワイ'` (NOT `destination_country='Hawaii'`; for country use 'USA').
- Example: "spring" / "20s" / "family" → `'spring' / '20s' / 'family'` (English code values).

### D.2 DISTINCT verification (REQUIRED before reporting 0 rows)
If a query returns 0 rows, IMMEDIATELY run `RETURN DISTINCT x.column_name` (GQL) or `SELECT DISTINCT column FROM ...` (SQL) to fetch the actual values present, then re-query using edit-distance or substring matching against those values.

### D.3 Query decomposition — MOST IMPORTANT rule for multi-step tool failures
When the FIRST tool call partially succeeded but a follow-up JOIN failed, RETURN AN ANSWER USING THE FIRST RESULT. Do NOT abort. Concretely:
1. Run the table-1 (booking) summary SQL/GQL → cache the result.
2. Run the table-2 (tour_review / cancellation / payment) as a SEPARATE SQL/GQL → cache the result.
3. Combine the two results in the response text (use prose JOIN when the structural JOIN failed).

### D.4 Relaxation rules (when multi-condition WHERE returns 0 rows)
DO NOT ask the user back. RELAX AUTOMATICALLY in this order:
(a) drop `season` → (b) drop `age_band` → (c) drop `customer_segment` → (d) widen `region` → `country` → (e) drop all conditions.
When you relax, label the response sections clearly: "Strict condition / Empty condition / Relaxed condition / Result".

### D.5 Timeout mitigation
- NEVER full-JOIN `itinerary_item` (175k rows). Filter on the `booking` side FIRST.
- ALWAYS apply TOP/LIMIT (10–30 rows).
- If the user did not specify a time range, default to "latest 12 months" or "all periods" and annotate the choice in the response.

### D.6 "Failure phrase" output gate
Before sending the final answer, scan it for the following phrases. If ANY is present, DISCARD the answer and re-run D.1–D.5:
- 「技術的なエラー」/「技術的制約」/「システム的な制約」/「集計クエリの制約」/「ツール側制限」/「ツール仕様により集計不可」
- 「SM 側で計算列が見えない」/「GROUP BY 構文の制約」/「自動集計ツールでは...動作しません」
- 「データ抽出ができませんでした」/「取得できませんでした」/「分析を実行できませんでした」
- "technical error" / "technical constraint" / "tool limitation" / "could not extract data" / "analysis failed"

INSTEAD OF returning these phrases, you MUST run one of: "§A value normalization retry", "§C template recompute", or "single-table decomposition", and return THAT result.

### D.7 Output language (CRITICAL)
- Reasoning is in English internally; the FINAL answer to the user MUST be written in 日本語 (Japanese).
- Numeric values: use `¥1,234,567` formatting; use `件` for booking count and `名` for traveler count.
- Tables: header row Japanese; data values as-is.

"""

EN_SECTION_F = f"""## §F. GQL Output Template (when analyze_ontology / NL2Ontology selects GQL — MUST follow this shape) <!-- §F-en {EN_VERSION} -->

The Fabric Data Agent generates NL2Ontology → GQL internally for queries that cannot be served by SQL alone (for example, a short prompt like "Show me Hawaii revenue"). SQL templates (§E) do not apply on the GQL path, so this section is the canonical pattern for GQL.

### F.1 Single-condition summary — `MATCH ... RETURN SUM/COUNT/AVG` only
```
MATCH (b:booking)
WHERE b.destination_region = "ハワイ"
  AND b.booking_status IN ["confirmed", "completed"]
RETURN SUM(b.total_revenue_jpy) AS revenue_jpy,
       COUNT(b) AS bookings,
       SUM(b.pax) AS travelers
```
**DO NOT include `b.booking_id` / `b.customer_id` / display-name columns in the RETURN clause** when aggregates are present (scalar + aggregate mix → invalid GQL → server_error).

### F.2 booking + customer JOIN — segment / age filtering
The ontology relationship name is `booking_has_customer` (CONFIRMED via ontology audit).
Use it whenever filtering by customer attributes (student / senior / family):

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

### F.3 Ranking — MUST include GROUP BY equivalent
```
MATCH (b:booking)
WHERE b.booking_status IN ["confirmed", "completed"]
RETURN b.destination_region AS region,
       SUM(b.total_revenue_jpy) AS revenue_jpy,
       COUNT(b) AS bookings,
       SUM(b.pax) AS travelers
ORDER BY revenue_jpy DESC LIMIT 10
```

"""

EN_SECTION_G = f"""## §G. GQL Anti-Patterns (FORBIDDEN — each one causes server_error) <!-- §G-en {EN_VERSION} -->

- ❌ **Including `booking_id` / `customer_id` / display columns in a RETURN clause that ALREADY has aggregates**
  → Cause #1 of "All variations failed". Either return aggregates ONLY, or expand the result with GROUP BY (no aggregate column on the same row).
- ❌ **Wrappers like `LOWER(b.destination_region) = LOWER("ハワイ")`**
  → Use direct equality (`=`) on the normalized string from the value mapping table. `LOWER()` is unnecessary AND defeats index usage.
- ❌ **`b.booking_status = "confirmed"` (single value)**
  ✅ Use **`b.booking_status IN ["confirmed", "completed"]`** instead
  (this is the convention for excluding cancellations and avoiding double-counting of revenue).
- ❌ **DO NOT translate `destination_region` values to English**: per the value mapping table, keep Japanese strings such as `"ハワイ" / "沖縄" / "京都"` AS-IS in WHERE. English mapping applies to the `country` column ONLY.
- ✅ `customer_segment` / `season` / `age_band` MUST use lowercase English codes (`"student" / "summer" / "20s"`). Japanese user input has already been mapped to English by the value mapping table.
"""


SECTION_RES = {
    "D": (re.compile(r"## §D\..*?(?=\n## §E\.)", re.DOTALL), EN_SECTION_D),
    "F": (re.compile(r"## §F\..*?(?=\n## §G\.)", re.DOTALL), EN_SECTION_F),
    "G": (re.compile(r"## §G\..*\Z", re.DOTALL), EN_SECTION_G),
}

VERSION_MARKER_RE = re.compile(rf"<!-- §[DFG]-en {re.escape(EN_VERSION)} -->")


def patch_instructions(instructions: str) -> tuple[str, bool]:
    """§D/§F/§G を英語版に置換。すでに同 version marker がある場合は no-op。

    Returns (new_instructions, changed).
    """
    if VERSION_MARKER_RE.search(instructions):
        already = len(VERSION_MARKER_RE.findall(instructions))
        if already >= 3:
            return instructions, False

    out = instructions
    for tag, (pat, repl) in SECTION_RES.items():
        m = pat.search(out)
        if not m:
            raise RuntimeError(f"§{tag} block not found — instructions may be malformed")
        # ensure replacement ends with single trailing newline so anchors stay clean
        replacement = repl.rstrip() + "\n"
        out = out[: m.start()] + replacement + out[m.end() :]
    return out, True


def patch_definition(definition: dict) -> tuple[dict, bool]:
    """draft + published の datasource.json 内 dataSourceInstructions を両方更新。"""
    import base64
    import json

    parts = definition["definition"]["parts"]
    changed_any = False
    for p in parts:
        path = p.get("path", "")
        if not path.endswith("datasource.json"):
            continue
        if "ontology-travelIQ_v2" not in path:
            continue
        raw = base64.b64decode(p["payload"]).decode("utf-8")
        obj = json.loads(raw)
        instructions = obj.get("dataSourceInstructions", "")
        new_instructions, did_change = patch_instructions(instructions)
        if not did_change:
            print(f"  - {path}: already at {EN_VERSION}, skipping")
            continue
        obj["dataSourceInstructions"] = new_instructions
        new_raw = json.dumps(obj, ensure_ascii=False, indent=2)
        p["payload"] = base64.b64encode(new_raw.encode("utf-8")).decode("ascii")
        print(f"  - {path}: patched ({len(instructions)} → {len(new_instructions)} chars)")
        changed_any = True
    return definition, changed_any


def main() -> None:
    import datetime as _dt
    import json

    print("=" * 70)
    print(f"Patch §D / §F / §G to English ({EN_VERSION})")
    print("=" * 70)

    print("\n[1/4] Fetching live definition via getDefinition LRO...")
    defn = fetch_live_definition()

    backup_dir = Path("scripts/fabric_data_overhaul/v2_artifacts/backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"agent_definition_pre_english_directives_{ts}.json"
    with backup_path.open("w", encoding="utf-8") as f:
        json.dump(defn, f, ensure_ascii=False, indent=2)
    print(f"  Backup saved: {backup_path}")

    print(f"\n[2/4] Applying §D/§F/§G English replacement (version: {EN_VERSION})...")
    defn, changed = patch_definition(defn)
    if not changed:
        print("  ALL parts already at version. No-op success.")
        return

    print("\n[3/4] Pushing updated definition via updateDefinition LRO...")
    push_definition(defn)

    print("\n[4/4] Done.")
    print(f"  Workspace: {WS_ID}")
    print(f"  Data Agent: {DA_ID}")
    print(f"  Live state: §D/§F/§G replaced with English {EN_VERSION}.")
    print(f"  Backup: {backup_path}")
    print("\n  To validate: python scripts/fabric_data_overhaul/v2_artifacts/smoke_demo_prompts.py")
    print("  D01 春の沖縄ファミリー must remain strict A (¥207M / 337件).")


if __name__ == "__main__":
    main()
