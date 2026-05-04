"""Decode live Fabric DA definition and inspect structure for Phase 11 planning.

Outputs:
- Part path + size
- For datasource.json: type, exampleQueries presence/count, dataSourceInstructions size
- For stage_config: aiInstructions size + first 600 chars
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import patch_demo_few_shot as helpers  # noqa: E402


def main() -> int:
    d = helpers.fetch_live_definition()
    parts = d["definition"]["parts"]
    print(f"live parts={len(parts)}\n")

    for p in parts:
        payload_b64 = p["payload"]
        payload_text = base64.b64decode(payload_b64).decode("utf-8")
        print(f"=== {p['path']} ({len(payload_b64)} b64 chars / {len(payload_text)} bytes) ===")
        try:
            obj = json.loads(payload_text)
        except Exception as e:
            print(f"  not JSON: {e}\n")
            continue

        keys = list(obj.keys())
        print(f"  top-level keys: {keys}")

        if "type" in obj:
            print(f"  type: {obj['type']}")
        if "artifactId" in obj:
            print(f"  artifactId: {obj['artifactId']}")
        if "exampleQueries" in obj:
            eq = obj["exampleQueries"]
            print(f"  exampleQueries: count={len(eq) if isinstance(eq, list) else 'NA'}")
        else:
            print("  exampleQueries: NOT PRESENT")
        if "sampleQueries" in obj:
            sq = obj["sampleQueries"]
            print(f"  sampleQueries: count={len(sq) if isinstance(sq, list) else 'NA'}")
        if "dataSourceInstructions" in obj:
            ins = obj["dataSourceInstructions"]
            print(f"  dataSourceInstructions: {len(ins) if ins else 0} chars")
            if ins:
                print(f"    preview: {ins[:200].replace(chr(10),' ')}")
        if "description" in obj:
            desc = obj["description"]
            if isinstance(desc, str):
                print(f"  description: {len(desc)} chars")
        if "aiInstructions" in obj:
            ai = obj["aiInstructions"]
            print(f"  aiInstructions: {len(ai)} chars")
            print(f"    head:  {ai[:400].replace(chr(10),' ')}")
            print(f"    tail:  {ai[-300:].replace(chr(10),' ')}")
        if "datasources" in obj:
            dsl = obj["datasources"]
            print(f"  datasources: count={len(dsl)}")
            for i, ds in enumerate(dsl):
                t = ds.get("type", "?")
                aid = ds.get("artifactId", "?")
                print(f"    [{i}] type={t} artifactId={aid}")
        if "elements" in obj:
            els = obj["elements"]
            print(f"  elements: count={len(els)}")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
