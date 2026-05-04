"""
Rollback Travel_Ontology_DA_v2 definition to a saved backup snapshot.

Usage:
    uv run python scripts/fabric_data_overhaul/v2_artifacts/rollback_to_backup.py [BACKUP_FILENAME]

If BACKUP_FILENAME is omitted, defaults to the pre-english-directives backup
(rollback target for the §D/§F/§G English patch).

The script:
    1. Loads the backup JSON (definition shape: {"definition": {"parts": [...]}})
    2. Re-fetches the live definition for diff display
    3. POSTs updateDefinition LRO with the backup parts
    4. Polls until Succeeded
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from scripts.fabric_data_overhaul.v2_artifacts.patch_demo_few_shot import (  # noqa: E402
    fetch_live_definition,
    push_definition,
)

DEFAULT_BACKUP_NAME = None  # Removed default — caller must specify explicit backup name to avoid restoring an unrelated snapshot.
BACKUP_DIR = Path(__file__).parent / "backups"


def main() -> int:
    if len(sys.argv) >= 2:
        backup_name = sys.argv[1]
    else:
        print("❌ Backup filename is required to avoid restoring an unrelated snapshot.")
        print("\nUsage:")
        print("    uv run python scripts/fabric_data_overhaul/v2_artifacts/rollback_to_backup.py BACKUP_FILENAME")
        print(f"\nAvailable backups in {BACKUP_DIR}:")
        if BACKUP_DIR.exists():
            for p in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
                print(f"  - {p.name}")
        return 1

    backup_path = BACKUP_DIR / backup_name
    if not backup_path.exists():
        print(f"❌ Backup not found: {backup_path}")
        print(f"\nAvailable backups in {BACKUP_DIR}:")
        for p in sorted(BACKUP_DIR.glob("*.json")):
            print(f"  - {p.name}")
        return 1

    print("=" * 70)
    print(f"Rollback to backup: {backup_name}")
    print("=" * 70)

    print(f"\n[1/3] Loading backup: {backup_path}")
    backup = json.loads(backup_path.read_text(encoding="utf-8"))
    parts = backup.get("definition", {}).get("parts", [])
    if not parts:
        print("❌ Backup has no parts")
        return 1
    print(f"  loaded {len(parts)} parts")

    print("\n[2/3] Fetching current live definition for diff record...")
    try:
        live = fetch_live_definition()
        live_parts = live.get("definition", {}).get("parts", [])
        print(f"  live has {len(live_parts)} parts (compare={'OK' if len(live_parts) == len(parts) else 'DIFF'})")
    except Exception as ex:
        print(f"  warning: could not fetch live definition: {ex}")

    print("\n[3/3] Pushing backup definition via updateDefinition LRO...")
    push_definition(backup)

    print("\n✅ Rollback complete.")
    print(f"  Restored from: {backup_name}")
    print("\n  To validate: uv run python scripts/fabric_data_overhaul/v2_artifacts/smoke_demo_prompts.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
