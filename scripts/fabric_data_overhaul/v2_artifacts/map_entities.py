"""Map entity ID -> name + property IDs from full ontology dump."""
import json, base64

with open("ontology_full.json", "r", encoding="utf-8") as fh:
    data = json.load(fh)

for p in data["definition"]["parts"]:
    if p["path"].endswith("/definition.json") and "EntityTypes" in p["path"]:
        eid = p["path"].split("/")[1]
        obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
        name = obj["name"]
        props = obj.get("properties", [])
        date_props = [(pr["name"], pr["id"]) for pr in props if pr.get("valueType") == "DateTime"]
        num_props = [(pr["name"], pr["id"]) for pr in props if pr.get("valueType") in ("BigInt", "Double")]
        print(f"\n=== {eid} {name} ===")
        print(f"  DateTime: {date_props}")
        print(f"  Numeric:  {num_props}")
