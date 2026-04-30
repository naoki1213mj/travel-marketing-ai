"""Patch ontology to enable timeseries on booking, payment, cancellation.

Strategy:
  - For each target entity, MOVE numeric metric properties from
    `properties` to `timeseriesProperties`.
  - Switch its DataBinding from NonTimeSeries -> TimeSeries with the
    chosen `timestampColumnName`.
  - Keep `propertyBindings` intact (all source columns continue mapping
    to their respective property IDs whether static or timeseries).

Targeted entities + chosen time field + metrics:
  booking      -> departure_date  : total_revenue_jpy, pax, pax_adult,
                                    pax_child, price_per_person_jpy,
                                    lead_time_days, duration_days
  payment      -> paid_at         : amount_jpy, exchange_rate_to_jpy,
                                    installment_count
  cancellation -> cancelled_at    : cancellation_lead_days,
                                    cancellation_fee_jpy,
                                    refund_amount_jpy

Output:
  ontology_patched.json  (full request body for updateDefinition)
"""
import json, base64

# ----- target plan -----------------------------------------------------------
PLAN = {
    "100000000002": {  # booking
        "timestampColumnName": "departure_date",
        "metric_names": [
            "total_revenue_jpy",
            "pax",
            "pax_adult",
            "pax_child",
            "price_per_person_jpy",
            "lead_time_days",
            "duration_days",
        ],
    },
    "100000000003": {  # payment
        "timestampColumnName": "paid_at",
        "metric_names": [
            "amount_jpy",
            "exchange_rate_to_jpy",
            "installment_count",
        ],
    },
    "100000000004": {  # cancellation
        "timestampColumnName": "cancelled_at",
        "metric_names": [
            "cancellation_lead_days",
            "cancellation_fee_jpy",
            "refund_amount_jpy",
        ],
    },
}

# ----- load full ontology ----------------------------------------------------
with open("ontology_full.json", "r", encoding="utf-8") as fh:
    data = json.load(fh)

new_parts = []
modified_entities = []
modified_bindings = []

for p in data["definition"]["parts"]:
    path = p["path"]

    # ---- entity definition ----
    is_entity_def = path.endswith("/definition.json") and "EntityTypes/" in path
    is_binding = "DataBindings/" in path

    if is_entity_def:
        eid = path.split("/")[1]
        if eid in PLAN:
            obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            metric_names = set(PLAN[eid]["metric_names"])
            old_props = obj.get("properties", [])
            keep_props = [pr for pr in old_props if pr["name"] not in metric_names]
            move_props = [pr for pr in old_props if pr["name"] in metric_names]
            assert len(move_props) == len(metric_names), (
                f"{eid}: expected {len(metric_names)} metrics, found {len(move_props)} "
                f"({[pr['name'] for pr in move_props]})"
            )
            obj["properties"] = keep_props
            obj["timeseriesProperties"] = move_props
            modified_entities.append((eid, obj["name"], len(move_props)))
            new_payload = base64.b64encode(
                json.dumps(obj, ensure_ascii=False).encode("utf-8")
            ).decode("utf-8")
            new_parts.append({"path": path, "payload": new_payload, "payloadType": p["payloadType"]})
            continue

    if is_binding:
        eid = path.split("/")[1]
        if eid in PLAN:
            obj = json.loads(base64.b64decode(p["payload"]).decode("utf-8"))
            cfg = obj["dataBindingConfiguration"]
            cfg["dataBindingType"] = "TimeSeries"
            cfg["timestampColumnName"] = PLAN[eid]["timestampColumnName"]
            modified_bindings.append((eid, PLAN[eid]["timestampColumnName"]))
            new_payload = base64.b64encode(
                json.dumps(obj, ensure_ascii=False).encode("utf-8")
            ).decode("utf-8")
            new_parts.append({"path": path, "payload": new_payload, "payloadType": p["payloadType"]})
            continue

    # passthrough (unmodified part) - skip .platform: not allowed in update
    if path == ".platform":
        continue
    new_parts.append({"path": path, "payload": p["payload"], "payloadType": p["payloadType"]})

req_body = {"definition": {"parts": new_parts}}

with open("ontology_patched.json", "w", encoding="utf-8") as fh:
    json.dump(req_body, fh, ensure_ascii=False, indent=2)

print("== Modified entities ==")
for eid, name, n in modified_entities:
    print(f"  {eid} {name:15s} : moved {n} metric(s) -> timeseriesProperties")
print("== Modified bindings ==")
for eid, ts in modified_bindings:
    print(f"  {eid} -> TimeSeries(timestampColumnName={ts})")
print(f"\nTotal parts in update body: {len(new_parts)} (excluded .platform)")
