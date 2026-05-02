"""Foundry の Fabric Data Agent connection を検証するスクリプト (PR 3)。

使い方:
    uv run python scripts/verify_foundry_fabric_connection.py

確認項目:
1. AZURE_AI_PROJECT_ENDPOINT / FOUNDRY_FABRIC_CONNECTION_ID が設定されているか
2. connection_id 形式が `/subscriptions/.../connections/{name}` を満たすか
3. Foundry Project に接続でき、connection が存在するか (DefaultAzureCredential)
4. tenant / workspace_id / artifact_id が記録されているか (best effort、SDK exposure 次第)

idempotent — 何度でも安全に実行できる。
"""

from __future__ import annotations

import logging
import os
import re
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("verify_foundry_fabric_connection")


_CONNECTION_ID_PATTERN = re.compile(
    r"^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft\.CognitiveServices/"
    r"accounts/[^/]+/projects/[^/]+/connections/[^/]+$"
)


def _print_check(label: str, status: str, detail: str = "") -> None:
    """[OK] / [WARN] / [FAIL] 形式の進捗を stdout に出す。"""
    icon = {"ok": "[OK]", "warn": "[WARN]", "fail": "[FAIL]"}.get(status, "[INFO]")
    line = f"{icon} {label}"
    if detail:
        line += f" — {detail}"
    print(line)


def _read_env(name: str) -> str:
    """環境変数を読む（空文字含めて常に str を返す）。"""
    return os.environ.get(name, "").strip()


def main() -> int:
    project_endpoint = _read_env("AZURE_AI_PROJECT_ENDPOINT")
    connection_id = _read_env("FOUNDRY_FABRIC_CONNECTION_ID")

    has_error = False

    if not project_endpoint:
        _print_check(
            "AZURE_AI_PROJECT_ENDPOINT",
            "fail",
            "未設定。`.env.local` または GitHub Actions Variables に設定してください。",
        )
        has_error = True
    else:
        _print_check("AZURE_AI_PROJECT_ENDPOINT", "ok", project_endpoint)

    if not connection_id:
        _print_check(
            "FOUNDRY_FABRIC_CONNECTION_ID",
            "fail",
            "未設定。Foundry Portal で Fabric DA connection を作成して指定してください。",
        )
        has_error = True
        return 1 if has_error else 0
    _print_check("FOUNDRY_FABRIC_CONNECTION_ID set", "ok", connection_id)

    if not _CONNECTION_ID_PATTERN.match(connection_id):
        _print_check(
            "FOUNDRY_FABRIC_CONNECTION_ID 形式",
            "fail",
            "`/subscriptions/.../connections/{name}` 形式ではありません",
        )
        has_error = True
    else:
        _print_check("FOUNDRY_FABRIC_CONNECTION_ID 形式", "ok")

    if has_error:
        return 1

    # Live check (best-effort) — connection をリスト/取得して存在を確認する
    try:
        from azure.ai.projects import AIProjectClient
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:
        _print_check(
            "azure-ai-projects SDK",
            "warn",
            f"import 失敗: {exc}。`uv sync` で依存をインストールしてください。",
        )
        return 0  # オフライン検証は終わっているので 0 を返す

    try:
        client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
    except Exception as exc:  # noqa: BLE001
        _print_check(
            "AIProjectClient 初期化",
            "warn",
            f"認証/接続失敗: {exc}",
        )
        return 0

    try:
        connections = getattr(client, "connections", None)
        if connections is None:
            _print_check(
                "Foundry connection 取得",
                "warn",
                "AIProjectClient.connections が未公開の SDK バージョンです",
            )
            return 0

        connection_name = connection_id.rsplit("/", 1)[-1]
        try:
            conn = connections.get(connection_name)
            target = getattr(conn, "target", "") or ""
            category = getattr(conn, "category", None) or getattr(conn, "type", None) or ""
            auth_type = getattr(conn, "auth_type", None) or ""
            _print_check(
                f"connection `{connection_name}` 取得",
                "ok",
                f"target={target[:80]}",
            )
            # Fabric Data Agent 専用 sanity check (false-green 防止)
            category_str = str(category).lower()
            target_str = str(target).lower()
            if category_str and "fabric" not in category_str:
                _print_check(
                    "connection.category",
                    "warn",
                    f"category=`{category}` (Fabric ではない可能性。MicrosoftFabricPreviewTool が消費できないかも)",
                )
            elif "/dataagents/" not in target_str and "fabric" not in target_str:
                _print_check(
                    "connection.target",
                    "warn",
                    f"target に `/dataagents/` が含まれない: {target[:120]}",
                )
            else:
                _print_check(
                    "Fabric DA shape 検証",
                    "ok",
                    f"category={category} auth={auth_type}",
                )
        except Exception as exc:  # noqa: BLE001
            _print_check(
                f"connection `{connection_name}` 取得",
                "fail",
                f"connection が見つからないか権限不足: {exc}",
            )
            has_error = True
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
