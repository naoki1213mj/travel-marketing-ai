"""Deep dependency probes for /api/ready/deep and post-cutover synthetic smoke.

これらのプローブは ACA の liveness/readiness probe には繋がない。
Container Apps の自動再起動を transient backend 障害で trigger しないため、
deep プローブは ops dashboard / nightly canary / cutover 後の手動検証専用とする。

設計原則 (rubber-duck `duck-prioritize-audits` の指摘 #2 参照):
- `/api/health` は固定値 200 (cheap liveness) を維持
- `/api/ready` は env var 必須項目チェック (shallow readiness) を維持
- `/api/ready/deep` (本モジュール) で実認可済の depend を 1 回ずつ実行する
  - Cosmos: 一時 doc を upsert + read + delete できるか
  - Foundry: Foundry Prompt Agent への agent_reference 解決
  - Foundry IQ: Azure AI Search KB に 1 件検索
  - Fabric Data Agent: 軽量 ping (assistants endpoint への POST)

各 probe は独立にタイムアウト + try/except で囲み、1 つの失敗で全体を 503 にせず、
個別に `ok=false, reason=...` を返す。同じバグ shape (Fabric MI 不在) は
このエンドポイントで catch される設計。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from src.config import get_settings

logger = logging.getLogger("travel.deep_health")

_PROBE_TIMEOUT_SECONDS = 8.0


async def _probe_cosmos() -> dict[str, Any]:
    """Cosmos DB に diagnostic doc を upsert → read → delete できるか確認する。"""
    settings = get_settings()
    if not settings.get("cosmos_db_endpoint"):
        return {"name": "cosmos", "ok": True, "skipped": True, "reason": "not_configured"}

    started = time.monotonic()
    diag_owner = "diag-deep-ready"
    diag_id = f"diag-{uuid.uuid4()}"
    try:
        from src.conversations import _get_container

        container = _get_container()
        if container is None:
            return {"name": "cosmos", "ok": False, "reason": "container_init_failed"}

        doc = {
            "id": diag_id,
            "user_id": diag_owner,
            "type": "diagnostic",
            "created_at": time.time(),
        }

        async def _roundtrip() -> None:
            await asyncio.to_thread(container.upsert_item, doc)
            await asyncio.to_thread(
                container.read_item,
                item=diag_id,
                partition_key=diag_owner,
            )
            await asyncio.to_thread(
                container.delete_item,
                item=diag_id,
                partition_key=diag_owner,
            )

        await asyncio.wait_for(_roundtrip(), timeout=_PROBE_TIMEOUT_SECONDS)
        return {
            "name": "cosmos",
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except asyncio.TimeoutError:
        return {"name": "cosmos", "ok": False, "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001 - probe 全体を握り潰さず詳細を返す
        return {
            "name": "cosmos",
            "ok": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


async def _probe_foundry_project() -> dict[str, Any]:
    """Foundry project endpoint への軽量 list_models 呼び出しで認可を検証する。"""
    settings = get_settings()
    endpoint = settings.get("project_endpoint", "")
    if not endpoint:
        return {"name": "foundry_project", "ok": True, "skipped": True, "reason": "not_configured"}

    started = time.monotonic()
    try:
        from src.agent_client import get_shared_credential

        cred = get_shared_credential()
        if cred is None:
            return {"name": "foundry_project", "ok": False, "reason": "credential_unavailable"}

        async def _ping() -> None:
            # AI Services / Foundry account endpoint のトークン取得を実行することで、
            # MI が AI Services のロールを正しく持っているかを実認可で確認する。
            await asyncio.to_thread(
                cred.get_token,
                "https://cognitiveservices.azure.com/.default",
            )

        await asyncio.wait_for(_ping(), timeout=_PROBE_TIMEOUT_SECONDS)
        return {
            "name": "foundry_project",
            "ok": True,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except asyncio.TimeoutError:
        return {"name": "foundry_project", "ok": False, "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "foundry_project",
            "ok": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


async def _probe_foundry_iq_search() -> dict[str, Any]:
    """Azure AI Search の知識ベースに 1 件 retrieve して認可と疎通を確認する。"""
    settings = get_settings()
    search_endpoint = settings.get("search_endpoint", "")
    if not search_endpoint:
        return {"name": "foundry_iq_search", "ok": True, "skipped": True, "reason": "not_configured"}

    started = time.monotonic()
    try:
        api_key = settings.get("search_api_key", "")
        if not api_key:
            return {"name": "foundry_iq_search", "ok": False, "reason": "api_key_missing"}

        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient

        client = SearchClient(
            endpoint=search_endpoint,
            index_name="regulations-index",
            credential=AzureKeyCredential(api_key),
        )

        async def _ping() -> int:
            results = await asyncio.to_thread(client.search, "テスト", top=1)
            return sum(1 for _ in results)

        count = await asyncio.wait_for(_ping(), timeout=_PROBE_TIMEOUT_SECONDS)
        return {
            "name": "foundry_iq_search",
            "ok": True,
            "result_count": count,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
        }
    except asyncio.TimeoutError:
        return {"name": "foundry_iq_search", "ok": False, "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "foundry_iq_search",
            "ok": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


async def _probe_fabric_data_agent() -> dict[str, Any]:
    """Fabric Data Agent v1 / v2 の assistants endpoint で auth を確認する。

    今回 cutover で 401 Unauthorized → CSV silent fallback だった shape を
    deep ready で catch するためのプローブ。
    """
    settings = get_settings()
    runtime_version = (settings.get("fabric_data_agent_runtime_version", "") or "").strip().lower()
    url = (
        settings.get("fabric_data_agent_url_v2", "")
        if runtime_version in {"v2", "2"}
        else settings.get("fabric_data_agent_url", "")
    )
    if not url:
        url = settings.get("fabric_data_agent_url", "") or settings.get("fabric_data_agent_url_v2", "")
    if not url:
        return {"name": "fabric_data_agent", "ok": True, "skipped": True, "reason": "not_configured"}

    started = time.monotonic()
    try:
        import httpx

        from src.agent_client import get_shared_credential

        cred = get_shared_credential()
        if cred is None:
            return {"name": "fabric_data_agent", "ok": False, "reason": "credential_unavailable"}

        token = await asyncio.to_thread(
            cred.get_token,
            "https://analysis.windows.net/powerbi/api/.default",
        )
        activity = str(uuid.uuid4())
        headers = {
            "Authorization": f"Bearer {token.token}",
            "ActivityId": activity,
            "x-ms-workload-resource-moniker": activity,
            "x-ms-ai-assistant-scenario": "aiskill",
            "x-ms-ai-aiskill-stage": "production",
            "Content-Type": "application/json",
        }
        base = url.rstrip("/")

        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
            r = await client.post(
                f"{base}/assistants?api-version=2024-05-01-preview",
                headers=headers,
                json={"model": "not used"},
            )
        if r.status_code == 200:
            return {
                "name": "fabric_data_agent",
                "ok": True,
                "runtime_version": runtime_version or "v1",
                "latency_ms": round((time.monotonic() - started) * 1000, 1),
            }
        return {
            "name": "fabric_data_agent",
            "ok": False,
            "reason": f"http_{r.status_code}",
            "body": r.text[:200],
        }
    except asyncio.TimeoutError:
        return {"name": "fabric_data_agent", "ok": False, "reason": "timeout"}
    except Exception as exc:  # noqa: BLE001
        return {
            "name": "fabric_data_agent",
            "ok": False,
            "reason": f"{type(exc).__name__}: {str(exc)[:160]}",
        }


async def run_all_probes() -> dict[str, Any]:
    """全 deep probe を並列実行して構造化レスポンスを返す。"""
    probes = await asyncio.gather(
        _probe_cosmos(),
        _probe_foundry_project(),
        _probe_foundry_iq_search(),
        _probe_fabric_data_agent(),
        return_exceptions=False,
    )
    failures = [p for p in probes if not p.get("ok") and not p.get("skipped")]
    return {
        "status": "ok" if not failures else "degraded",
        "checked_at": time.time(),
        "probes": probes,
        "failure_count": len(failures),
    }
