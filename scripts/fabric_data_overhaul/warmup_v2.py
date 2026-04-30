"""Fabric Data Agent v2 capacity warmup script.

デモ開始 5〜10 分前に実行することで、Fabric capacity の cold start による
NL2Ontology の `submit_tool_outputs BadRequest` を回避する。

Phase 9.6 で grade A 確定の代表 4 prompt を 1 巡だけ流して capacity を温める。
失敗してもスクリプトは exit 0 する（あくまでベストエフォート）。

Usage:
    uv run python scripts/fabric_data_overhaul/warmup_v2.py

Required env / azure context:
    - `az login` で Fabric workspace `ws-3iq-demo` の Viewer 以上を持つ identity が認証済みであること。
    - 環境変数 `FABRIC_DATA_AGENT_URL_V2` または既定の Travel_Ontology_DA_v2 URL を使う。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid

WORKSPACE_ID = "096ff72a-6174-4aba-8f0c-140454fa6c3f"
DA_V2_ID = "b85b67a4-bac4-4852-95e1-443c02032844"
DEFAULT_BASE_URL = (
    f"https://api.fabric.microsoft.com/v1/workspaces/{WORKSPACE_ID}"
    f"/dataagents/{DA_V2_ID}/aiassistant/openai"
)

WARMUP_PROMPTS: list[tuple[str, str]] = [
    ("D1", "2024年に最も売上が伸びた destination_region をランキングで教えてください"),
    ("D2", "学生向けの春の沖縄予約件数は？"),
    ("D3", "ハワイの夏のリピート顧客比率を教えてください"),
    ("D5", "直近 3 年の月別売上推移を教えてください"),
]

TIMEOUT_S = 120


def get_token() -> str:
    """az CLI 経由で Power BI API audience の token を取得する。"""
    result = subprocess.run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            "https://analysis.windows.net/powerbi/api",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        shell=sys.platform.startswith("win"),
        check=True,
    )
    return result.stdout.strip()


def make_client(token: str, base_url: str):
    from openai import OpenAI

    activity_id = str(uuid.uuid4())
    return OpenAI(
        base_url=base_url,
        api_key="dummy",
        default_headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "ActivityId": activity_id,
            "x-ms-workload-resource-moniker": activity_id,
            "x-ms-ai-assistant-scenario": "aiskill",
            "x-ms-ai-aiskill-stage": "production",
        },
        default_query={"api-version": "2024-05-01-preview"},
        timeout=TIMEOUT_S,
    )


def warmup_one(client, qid: str, question: str) -> str:
    """1 prompt 流してステータスを返す。例外は捕捉してスキップ判定。"""
    try:
        assistant = client.beta.assistants.create(model="not used")
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(thread_id=thread.id, role="user", content=question)
        run = client.beta.threads.runs.create(thread_id=thread.id, assistant_id=assistant.id)

        deadline = time.time() + TIMEOUT_S
        while time.time() < deadline:
            run = client.beta.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
            if run.status in {"completed", "failed", "cancelled", "expired"}:
                break
            time.sleep(2)
        return run.status or "unknown"
    except (RuntimeError, ValueError, OSError) as exc:
        return f"error:{type(exc).__name__}:{exc}"


def main() -> int:
    base_url = os.environ.get("FABRIC_DATA_AGENT_URL_V2", DEFAULT_BASE_URL)
    print(f"Warmup target: {base_url}")

    try:
        token = get_token()
    except subprocess.CalledProcessError as exc:
        print(f"[skip] az CLI token fetch failed: {exc}", file=sys.stderr)
        return 0

    client = make_client(token, base_url)

    success = 0
    for qid, question in WARMUP_PROMPTS:
        print(f"[{qid}] {question}", flush=True)
        started = time.time()
        status = warmup_one(client, qid, question)
        elapsed = time.time() - started
        marker = "OK " if status == "completed" else "WARN"
        print(f"  {marker} status={status} elapsed={elapsed:.1f}s", flush=True)
        if status == "completed":
            success += 1

    print(f"\nWarmup complete: {success}/{len(WARMUP_PROMPTS)} prompts succeeded.")
    print("Capacity should now be warm. Proceed with the live demo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
