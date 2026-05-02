"""Inspect conversation 84d2a335 from Cosmos to debug regression bug."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.conversations import get_conversation


async def main():
    cid = sys.argv[1] if len(sys.argv) > 1 else "84d2a335-ac76-439d-b981-2c8a3f4da3b8"
    doc = await get_conversation(cid, allow_cross_owner=True)
    if not doc:
        print(f"NOT FOUND: {cid}")
        return
    print(f"id: {doc.get('id')}")
    print(f"user_id: {doc.get('user_id')}")
    print(f"status: {doc.get('status')}")
    metadata = doc.get("metadata", {}) or {}
    print(f"has_pending_approval_token: {bool(metadata.get('pending_approval_token'))}")
    msgs = doc.get("messages", []) or []
    print(f"messages count: {len(msgs)}")
    print("---all events:")
    for i, ev in enumerate(msgs):
        ev_name = ev.get("event", "?")
        data = ev.get("data", {}) or {}
        agent = data.get("agent", "") if isinstance(data, dict) else ""
        ct = data.get("content_type", "") if isinstance(data, dict) else ""
        sc = data.get("approval_scope", "") if isinstance(data, dict) else ""
        ver = data.get("version", "") if isinstance(data, dict) else ""
        status_field = data.get("status", "") if isinstance(data, dict) else ""
        print(f"  [{i:3d}] {ev_name:20s} agent={agent:25s} ct={ct:8s} scope={sc:8s} ver={ver} status={status_field}")


if __name__ == "__main__":
    asyncio.run(main())
