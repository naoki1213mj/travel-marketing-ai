"""postprovision.py — azd provision 後の APIM AI Gateway + Foundry 統合セットアップ

azd の postprovision フックとして実行される。
1. Foundry に AI Gateway 接続（APIM → Foundry）を作成
2. Foundry が自動生成する foundry-* API に AI Gateway ポリシーを適用

冪等: 何度実行しても安全。失敗時はログ出力のみで中断しない。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition
from azure.core.exceptions import ClientAuthenticationError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent / "mcp_server"
_FUNCTION_RUNTIME = "python"
_FUNCTION_RUNTIME_VERSION = "3.13"


def _resolve_cli(name: str) -> str:
    """Windows でも実行できる CLI 実体を解決する。"""
    for candidate in (name, f"{name}.exe", f"{name}.cmd", f"{name}.bat"):
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return name


def _cli_available(name: str) -> bool:
    """CLI が利用可能かを判定する。"""
    for candidate in (name, f"{name}.exe", f"{name}.cmd", f"{name}.bat"):
        if shutil.which(candidate):
            return True
    return False


def _run_cli(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """az / azd などの CLI を OS 非依存に実行する。"""
    resolved_command = [_resolve_cli(command[0]), *command[1:]]
    run_kwargs = {
        "check": False,
        "text": True,
        **kwargs,
    }
    try:
        return subprocess.run(resolved_command, **run_kwargs)
    except FileNotFoundError as exc:
        logger.warning("CLI が見つかりません: %s (%s)", command[0], exc)
        return subprocess.CompletedProcess(resolved_command, 127, stdout="", stderr=str(exc))


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------


def _get_azd_env() -> dict[str, str]:
    """azd env get-values から環境変数を読み込む"""
    if not _cli_available("azd"):
        logger.info("azd が見つからないため azd env の読み込みをスキップします")
        return {}

    result = _run_cli(
        ["azd", "env", "get-values"],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("azd env get-values に失敗: %s", result.stderr.strip())
        return {}

    env: dict[str, str] = {}
    for line in result.stdout.strip().split("\n"):
        if "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"')
    return env


def _merge_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """azd env とプロセス環境変数をマージする。"""
    merged = dict(base_env or {})
    for key, value in os.environ.items():
        if value:
            merged[key] = value
    return merged


def _set_azd_env_value(key: str, value: str) -> bool:
    """利用可能な場合のみ azd env へ値を保存する。"""
    if not value:
        return False
    if not _cli_available("azd"):
        logger.info("azd が見つからないため azd env への保存をスキップします: %s", key)
        return False

    result = _run_cli(["azd", "env", "set", key, value], capture_output=True)
    if result.returncode == 0:
        return True

    logger.info("azd env への保存をスキップします: %s", key)
    return False


def _normalize_resource_token(container_app_name: str) -> str:
    """Container App 名から共通 resource token を取り出す。"""
    normalized = container_app_name.strip()
    return normalized[3:] if normalized.startswith("ca-") else normalized


def _derive_improvement_mcp_names(env: dict[str, str]) -> tuple[str, str]:
    """improvement-mcp 用の Function App 名と storage account 名を決める。"""
    function_app_name = env.get("IMPROVEMENT_MCP_FUNCTION_APP_NAME", "").strip()
    storage_account_name = env.get("IMPROVEMENT_MCP_STORAGE_ACCOUNT_NAME", "").strip()

    if function_app_name and storage_account_name:
        return function_app_name, storage_account_name

    resource_token = _normalize_resource_token(env.get("AZURE_CONTAINER_APP_NAME", ""))
    if not resource_token:
        return function_app_name, storage_account_name

    if not function_app_name:
        function_app_name = f"func-mcp-{resource_token}"
    if not storage_account_name:
        storage_account_name = f"stfn{resource_token}"
    return function_app_name, storage_account_name


def _resolve_resource_group_location(resource_group: str, configured_location: str = "") -> str:
    """resource group の location を解決する。"""
    if configured_location.strip():
        return configured_location.strip()

    result = _run_cli(
        ["az", "group", "show", "--name", resource_group, "--query", "location", "-o", "tsv"],
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()

    logger.warning("resource group の location 取得に失敗しました: %s", result.stderr.strip())
    return ""


def _sync_improvement_mcp_env(function_app_name: str, function_app_rg: str, storage_account_name: str) -> None:
    """次回以降の azd 実行でも同じ値を再利用できるよう env へ保存する。"""
    if not _cli_available("azd"):
        logger.info("azd が見つからないため improvement-mcp の env 保存をスキップします")
        return

    for key, value in {
        "IMPROVEMENT_MCP_FUNCTION_APP_NAME": function_app_name,
        "IMPROVEMENT_MCP_FUNCTION_APP_RESOURCE_GROUP": function_app_rg,
        "IMPROVEMENT_MCP_STORAGE_ACCOUNT_NAME": storage_account_name,
    }.items():
        _set_azd_env_value(key, value)


def _update_container_app_env(container_app_name: str, resource_group: str, env_vars: dict[str, str]) -> None:
    """Container App の環境変数を同期する。"""
    if not container_app_name or not resource_group:
        return

    pairs = [f"{key}={value}" for key, value in env_vars.items() if value]
    if not pairs:
        return

    result = _run_cli(
        [
            "az",
            "containerapp",
            "update",
            "--name",
            container_app_name,
            "--resource-group",
            resource_group,
            "--set-env-vars",
            *pairs,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("Container App の env 同期に失敗しました: %s", result.stderr.strip())
        return

    logger.info("Container App の env を同期しました: %s", container_app_name)


def _get_token(scope: str = "https://management.azure.com/.default") -> str:
    """DefaultAzureCredential でアクセストークンを取得"""
    credential = DefaultAzureCredential()
    return credential.get_token(scope).token


def _rest_call(
    url: str,
    *,
    method: str = "GET",
    body: dict | None = None,
    token: str | None = None,
    scope: str = "https://management.azure.com/.default",
    timeout: int = 30,
) -> dict | None:
    """Azure REST API を呼び出すヘルパー"""
    if token is None:
        try:
            token = _get_token(scope)
        except ClientAuthenticationError as exc:
            logger.warning("REST %s %s → token acquisition failed: %s", method, url, exc)
            return None
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("REST %s %s → token acquisition failed: %s", method, url, exc)
            return None

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()
        except Exception:
            pass
        logger.warning("REST %s %s → HTTP %s: %s", method, url, e.code, error_body[:500])
        return None
    except Exception as e:
        logger.warning("REST %s %s → %s", method, url, e)
        return None


# ---------------------------------------------------------------------------
# Step 1: Foundry AI Gateway 接続を作成
# ---------------------------------------------------------------------------


def create_ai_gateway_connection(
    project_endpoint: str,
    apim_name: str,
    apim_resource_id: str,
) -> bool:
    """Foundry に AI Gateway 接続を作成する

    APIM を経由して LLM 推論をルーティングするための接続を登録する。
    成功すると Foundry が APIM 上に foundry-* API を自動生成する。
    """
    connection_name = "travel-ai-gateway"
    # Foundry connections は ARM API 経由で管理する
    # project_endpoint（AI Services 形式）ではなく ARM パスを使用
    ai_services_name = project_endpoint.split("//")[1].split(".")[0]  # ais5gg4m4g72lrdo
    project_name = project_endpoint.rstrip("/").split("/")[-1]  # aip-5gg4m4g72lrdo
    url = (
        f"https://management.azure.com{apim_resource_id.rsplit('/providers/Microsoft.ApiManagement', 1)[0]}"
        f"/providers/Microsoft.CognitiveServices/accounts/{ai_services_name}"
        f"/projects/{project_name}/connections/{connection_name}"
        "?api-version=2025-04-01-preview"
    )

    body = {
        "properties": {
            "authType": "ProjectManagedIdentity",
            "category": "ApiManagement",
            "target": f"https://{apim_name}.azure-api.net",
            "isSharedToAll": True,
            "metadata": {
                "ResourceId": apim_resource_id,
                "ApiType": "Azure",
            },
        },
    }

    logger.info("AI Gateway 接続を作成中: %s → %s", connection_name, apim_name)
    # Foundry connections API は ARM 経由でアクセスする
    # project_endpoint ではなく management.azure.com を使用
    result = _rest_call(url, method="PUT", body=body, scope="https://management.azure.com/.default")
    if result is not None:
        logger.info("AI Gateway 接続を作成しました: %s", connection_name)
        return True

    logger.warning("AI Gateway 接続の作成に失敗しました")
    return False


# ---------------------------------------------------------------------------
# Step 2: foundry-* API に AI Gateway ポリシーを適用
# ---------------------------------------------------------------------------

_AI_GATEWAY_POLICY_XML = """\
<policies>
  <inbound>
    <base />
    <llm-token-limit
      tokens-per-minute="80000"
      counter-key="@(context.Subscription.Id)"
      estimate-prompt-tokens="true"
      remaining-tokens-header-name="x-ratelimit-remaining-tokens" />
    <llm-emit-token-metric>
      <dimension name="API ID" />
    </llm-emit-token-metric>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>"""

_APIM_API_VERSION = "2024-10-01-preview"
_FUNCTION_APP_API_VERSION = "2024-04-01"
_IMPROVEMENT_MCP_API_ID = "improvement-mcp"
_IMPROVEMENT_MCP_BACKEND_ID = "improvement-mcp-backend"
_IMPROVEMENT_MCP_NAMED_VALUE = "func-mcp-extension-key"
_IMPROVEMENT_MCP_READY_ATTEMPTS = 6
_IMPROVEMENT_MCP_READY_DELAY_SECONDS = 10
_IMPROVEMENT_MCP_RBAC_WAIT_SECONDS = 30
_STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE = "Storage Blob Data Contributor"
_STORAGE_QUEUE_DATA_CONTRIBUTOR_ROLE = "Storage Queue Data Contributor"
_STORAGE_QUEUE_DATA_MESSAGE_PROCESSOR_ROLE = "Storage Queue Data Message Processor"
_IMPROVEMENT_MCP_POLICY_XML = """\
<policies>
  <inbound>
    <base />
    <set-header name="x-functions-key" exists-action="override">
      <value>{{func-mcp-extension-key}}</value>
    </set-header>
  </inbound>
  <backend>
    <base />
  </backend>
  <outbound>
    <base />
  </outbound>
  <on-error>
    <base />
  </on-error>
</policies>"""


def _build_mcp_package() -> Path | None:
    """mcp_server ディレクトリを zip 化して返す。"""
    if not _MCP_SERVER_DIR.exists():
        logger.warning("mcp_server ディレクトリが見つかりません: %s", _MCP_SERVER_DIR)
        return None

    with tempfile.NamedTemporaryFile(prefix="improvement-mcp-", suffix=".zip", delete=False) as temp_file:
        package_path = Path(temp_file.name)

    try:
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in _MCP_SERVER_DIR.rglob("*"):
                if file_path.is_dir():
                    continue
                relative_path = file_path.relative_to(_MCP_SERVER_DIR)
                if any(part in {".venv", "__pycache__", ".pytest_cache"} for part in relative_path.parts):
                    continue
                if file_path.suffix in {".pyc", ".pyo"}:
                    continue
                archive.write(file_path, arcname=relative_path.as_posix())
        return package_path
    except (OSError, ValueError) as exc:
        logger.warning("MCP package 作成に失敗しました: %s", exc)
        package_path.unlink(missing_ok=True)
        return None


def ensure_storage_account(resource_group: str, location: str, storage_account_name: str) -> bool:
    """Flex Consumption 用 storage account を用意する。"""
    show_result = _run_cli(
        ["az", "storage", "account", "show", "--name", storage_account_name, "--resource-group", resource_group],
        capture_output=True,
    )
    if show_result.returncode == 0:
        logger.info("MCP storage account 既存: %s", storage_account_name)
    else:
        create_result = _run_cli(
            [
                "az",
                "storage",
                "account",
                "create",
                "--name",
                storage_account_name,
                "--resource-group",
                resource_group,
                "--location",
                location,
                "--sku",
                "Standard_LRS",
                "--kind",
                "StorageV2",
                "--allow-shared-key-access",
                "false",
                "--allow-blob-public-access",
                "false",
                "--min-tls-version",
                "TLS1_2",
                "--public-network-access",
                "Enabled",
            ],
            capture_output=True,
        )
        if create_result.returncode == 0:
            logger.info("MCP storage account を作成しました: %s", storage_account_name)
        else:
            logger.warning("MCP storage account の作成に失敗しました: %s", create_result.stderr.strip())
            return False

    update_result = _run_cli(
        [
            "az",
            "storage",
            "account",
            "update",
            "--name",
            storage_account_name,
            "--resource-group",
            resource_group,
            "--allow-shared-key-access",
            "false",
            "--allow-blob-public-access",
            "false",
            "--min-tls-version",
            "TLS1_2",
            "--public-network-access",
            "Enabled",
        ],
        capture_output=True,
    )
    if update_result.returncode == 0:
        logger.info("MCP storage account のネットワーク/認証設定を更新しました: %s", storage_account_name)
        return True

    logger.warning("MCP storage account の設定更新に失敗しました: %s", update_result.stderr.strip())
    return False


def ensure_improvement_mcp_function_app(
    resource_group: str,
    location: str,
    function_app_name: str,
    storage_account_name: str,
) -> bool:
    """improvement-mcp 用 Function App を用意する。"""
    show_result = _run_cli(
        ["az", "functionapp", "show", "--name", function_app_name, "--resource-group", resource_group],
        capture_output=True,
    )
    if show_result.returncode == 0:
        logger.info("improvement-mcp Function App 既存: %s", function_app_name)
        return True

    create_result = _run_cli(
        [
            "az",
            "functionapp",
            "create",
            "--resource-group",
            resource_group,
            "--name",
            function_app_name,
            "--storage-account",
            storage_account_name,
            "--flexconsumption-location",
            location,
            "--runtime",
            _FUNCTION_RUNTIME,
            "--runtime-version",
            _FUNCTION_RUNTIME_VERSION,
        ],
        capture_output=True,
    )
    if create_result.returncode == 0:
        logger.info("improvement-mcp Function App を作成しました: %s", function_app_name)
        return True

    logger.warning("improvement-mcp Function App の作成に失敗しました: %s", create_result.stderr.strip())
    return False


def _ensure_function_app_identity(function_app_name: str, resource_group: str) -> str | None:
    """Function App に system assigned managed identity を付与する。"""
    result = _run_cli(
        [
            "az",
            "functionapp",
            "identity",
            "assign",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("Function App の managed identity 付与に失敗しました: %s", result.stderr.strip())
        return None

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {}

    principal_id = str(payload.get("principalId") or "").strip()
    if not principal_id:
        logger.warning("Function App の principalId を取得できません: %s", function_app_name)
        return None

    return principal_id


def _get_storage_account_resource_id(resource_group: str, storage_account_name: str) -> str:
    """storage account の resource id を返す。"""
    result = _run_cli(
        [
            "az",
            "storage",
            "account",
            "show",
            "--name",
            storage_account_name,
            "--resource-group",
            resource_group,
            "--query",
            "id",
            "-o",
            "tsv",
        ],
        capture_output=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()

    logger.warning("storage account resource id の取得に失敗しました: %s", result.stderr.strip())
    return ""


def _ensure_storage_role_assignment(storage_account_id: str, principal_id: str, role_name: str) -> bool:
    """Function App の managed identity に host storage 用ロールを付与する。"""
    list_result = _run_cli(
        [
            "az",
            "role",
            "assignment",
            "list",
            "--assignee-object-id",
            principal_id,
            "--scope",
            storage_account_id,
            "--role",
            role_name,
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if list_result.returncode == 0:
        try:
            assignments = json.loads(list_result.stdout)
        except json.JSONDecodeError:
            assignments = []

        if isinstance(assignments, list) and assignments:
            logger.info("%s ロール割り当て既存: %s", role_name, principal_id)
            return True

    create_result = _run_cli(
        [
            "az",
            "role",
            "assignment",
            "create",
            "--assignee-object-id",
            principal_id,
            "--assignee-principal-type",
            "ServicePrincipal",
            "--role",
            role_name,
            "--scope",
            storage_account_id,
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if create_result.returncode == 0:
        logger.info("%s ロールを付与しました: %s", role_name, principal_id)
        return True

    stderr = create_result.stderr.strip()
    if "already exists" in stderr.lower():
        logger.info("%s ロール割り当て既存: %s", role_name, principal_id)
        return True

    logger.warning("%s ロール付与に失敗しました: %s", role_name, stderr)
    return False


def _get_deployment_storage_container_name(function_app_name: str, resource_group: str) -> str:
    """Function App の deployment storage container 名を返す。"""
    result = _run_cli(
        [
            "az",
            "functionapp",
            "deployment",
            "config",
            "show",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("Function App deployment config の取得に失敗しました: %s", result.stderr.strip())
        return ""

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("Function App deployment config の JSON 解析に失敗しました: %s", function_app_name)
        return ""

    storage = payload.get("storage") if isinstance(payload, dict) else None
    deployment_value = str(storage.get("value") or "").strip() if isinstance(storage, dict) else ""
    if not deployment_value:
        logger.warning("Function App deployment storage の URL を取得できません: %s", function_app_name)
        return ""

    parsed_path = urlparse(deployment_value).path.strip("/")
    if not parsed_path:
        logger.warning("Function App deployment storage container 名を解決できません: %s", function_app_name)
        return ""

    container_name, *_ = parsed_path.split("/", 1)
    return container_name


def ensure_improvement_mcp_managed_identity_storage(
    resource_group: str,
    function_app_name: str,
    storage_account_name: str,
) -> bool:
    """Improvement MCP Function App を keyless storage 構成へ揃える。"""
    principal_id = _ensure_function_app_identity(function_app_name, resource_group)
    if not principal_id:
        return False

    storage_account_id = _get_storage_account_resource_id(resource_group, storage_account_name)
    if not storage_account_id:
        return False

    required_roles = (
        _STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE,
        _STORAGE_QUEUE_DATA_CONTRIBUTOR_ROLE,
        _STORAGE_QUEUE_DATA_MESSAGE_PROCESSOR_ROLE,
    )
    for role_name in required_roles:
        if not _ensure_storage_role_assignment(storage_account_id, principal_id, role_name):
            return False

    deployment_container_name = _get_deployment_storage_container_name(function_app_name, resource_group)
    if not deployment_container_name:
        return False

    appsettings_result = _run_cli(
        [
            "az",
            "functionapp",
            "config",
            "appsettings",
            "set",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
            "--settings",
            f"AzureWebJobsStorage__accountName={storage_account_name}",
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if appsettings_result.returncode != 0:
        logger.warning("Function App の AzureWebJobsStorage 設定更新に失敗しました: %s", appsettings_result.stderr.strip())
        return False

    delete_result = _run_cli(
        [
            "az",
            "functionapp",
            "config",
            "appsettings",
            "delete",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
            "--setting-names",
            "AzureWebJobsStorage",
            "DEPLOYMENT_STORAGE_CONNECTION_STRING",
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if delete_result.returncode != 0:
        logger.warning("Function App の旧 storage 設定削除に失敗しました: %s", delete_result.stderr.strip())
        return False

    deployment_config_result = _run_cli(
        [
            "az",
            "functionapp",
            "deployment",
            "config",
            "set",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
            "--deployment-storage-name",
            storage_account_name,
            "--deployment-storage-container-name",
            deployment_container_name,
            "--deployment-storage-auth-type",
            "SystemAssignedIdentity",
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if deployment_config_result.returncode != 0:
        logger.warning("Function App の deployment storage 設定更新に失敗しました: %s", deployment_config_result.stderr.strip())
        return False

    restart_result = _run_cli(
        [
            "az",
            "functionapp",
            "restart",
            "--name",
            function_app_name,
            "--resource-group",
            resource_group,
        ],
        capture_output=True,
    )
    if restart_result.returncode != 0:
        logger.warning("Function App の再起動に失敗しました: %s", restart_result.stderr.strip())
        return False

    logger.info(
        "improvement-mcp Function App を keyless storage 構成へ更新しました: %s",
        function_app_name,
    )
    logger.info(
        "managed identity の RBAC 反映待ち (%d秒): %s",
        _IMPROVEMENT_MCP_RBAC_WAIT_SECONDS,
        function_app_name,
    )
    time.sleep(_IMPROVEMENT_MCP_RBAC_WAIT_SECONDS)
    return True


def deploy_improvement_mcp_function(
    resource_group: str,
    location: str,
    function_app_name: str,
    storage_account_name: str,
) -> bool:
    """Flex Consumption の Function App を作成し、improvement MCP を配備する。"""
    if not ensure_storage_account(resource_group, location, storage_account_name):
        return False
    if not ensure_improvement_mcp_function_app(resource_group, location, function_app_name, storage_account_name):
        return False
    if not ensure_improvement_mcp_managed_identity_storage(resource_group, function_app_name, storage_account_name):
        return False

    package_path = _build_mcp_package()
    if package_path is None:
        return False

    try:
        logger.info(
            "improvement-mcp Function App へコードを配備します。Flex Consumption の remote build に数分かかる場合があります: %s",
            function_app_name,
        )
        deploy_started = time.perf_counter()
        deploy_result = _run_cli(
            [
                "az",
                "functionapp",
                "deployment",
                "source",
                "config-zip",
                "--src",
                str(package_path),
                "--name",
                function_app_name,
                "--resource-group",
                resource_group,
                "--build-remote",
                "true",
            ],
            capture_output=True,
        )
        deploy_elapsed_seconds = time.perf_counter() - deploy_started
    finally:
        package_path.unlink(missing_ok=True)

    if deploy_result.returncode == 0:
        logger.info(
            "improvement-mcp Function App にコードを配備しました: %s (%.1f秒)",
            function_app_name,
            deploy_elapsed_seconds,
        )
        return True

    logger.warning(
        "improvement-mcp の配備に失敗しました: %s (%.1f秒)",
        deploy_result.stderr.strip(),
        deploy_elapsed_seconds,
    )
    return False


def setup_improvement_mcp(subscription_id: str, rg: str, apim_name: str, env: dict[str, str]) -> bool:
    """improvement-mcp Function App の配備と APIM 登録をまとめて実行する。"""
    function_app_name, storage_account_name = _derive_improvement_mcp_names(env)
    if not function_app_name:
        logger.info("improvement-mcp 用 Function App 名を解決できないためスキップします")
        return False

    function_app_rg = env.get("IMPROVEMENT_MCP_FUNCTION_APP_RESOURCE_GROUP", "").strip() or rg
    location = _resolve_resource_group_location(function_app_rg, env.get("AZURE_LOCATION", ""))

    deployed = False
    management_token: str | None = None
    if storage_account_name and location:
        management_token = _get_token()
        deployed = deploy_improvement_mcp_function(
            resource_group=function_app_rg,
            location=location,
            function_app_name=function_app_name,
            storage_account_name=storage_account_name,
        )
        if not deployed:
            logger.warning("improvement-mcp の自動配備に失敗したため APIM 登録を中断します")
            return False
        _sync_improvement_mcp_env(function_app_name, function_app_rg, storage_account_name)
    else:
        logger.info(
            "improvement-mcp の自動配備をスキップします (location=%s, storage=%s)",
            bool(location),
            bool(storage_account_name),
        )

    readiness_attempts = _IMPROVEMENT_MCP_READY_ATTEMPTS if deployed else 1
    configured = configure_improvement_mcp(
        subscription_id=subscription_id,
        rg=rg,
        apim_name=apim_name,
        function_app_name=function_app_name,
        function_app_rg=function_app_rg,
        readiness_attempts=readiness_attempts,
        token=management_token,
    )
    return configured


def _apim_resource_url(subscription_id: str, rg: str, apim_name: str, suffix: str) -> str:
    """APIM 管理プレーンのリソース URL を返す。"""
    return (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}{suffix}"
        f"?api-version={_APIM_API_VERSION}"
    )


def _function_app_resource_url(subscription_id: str, rg: str, function_app_name: str, suffix: str = "") -> str:
    """Function App 管理プレーンのリソース URL を返す。"""
    return (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.Web/sites/{function_app_name}{suffix}"
        f"?api-version={_FUNCTION_APP_API_VERSION}"
    )


def _get_function_app_mcp_details(
    subscription_id: str,
    rg: str,
    function_app_name: str,
    token: str | None = None,
) -> tuple[str, str] | None:
    """Function App の公開 URL と mcp_extension system key を返す。"""
    site = _rest_call(_function_app_resource_url(subscription_id, rg, function_app_name), token=token)
    properties = site.get("properties") if isinstance(site, dict) else None
    if not isinstance(properties, dict):
        logger.warning("Function App の取得に失敗しました: %s", function_app_name)
        return None

    default_host_name = str(properties.get("defaultHostName") or "").strip()
    if not default_host_name:
        logger.warning("Function App の defaultHostName が取得できません: %s", function_app_name)
        return None

    keys = _rest_call(
        _function_app_resource_url(subscription_id, rg, function_app_name, "/host/default/listKeys"),
        method="POST",
        token=token,
    )
    system_keys = keys.get("systemKeys") if isinstance(keys, dict) else None
    if not isinstance(system_keys, dict):
        logger.warning("Function App の systemKeys が取得できません: %s", function_app_name)
        return None

    mcp_extension_key = str(system_keys.get("mcp_extension") or "").strip()
    if not mcp_extension_key:
        logger.warning("mcp_extension system key が見つかりません: %s", function_app_name)
        return None

    return f"https://{default_host_name}", mcp_extension_key


def _wait_for_function_app_mcp_details(
    subscription_id: str,
    rg: str,
    function_app_name: str,
    attempts: int,
    delay_seconds: int,
    token: str | None = None,
) -> tuple[str, str] | None:
    """Function App の MCP 詳細が利用可能になるまで待機する。"""
    total_attempts = max(1, attempts)
    for attempt in range(1, total_attempts + 1):
        function_details = _get_function_app_mcp_details(subscription_id, rg, function_app_name, token=token)
        if function_details is not None:
            return function_details
        if attempt >= total_attempts:
            break
        logger.info(
            "improvement-mcp Function App の準備待機中 (%s/%s): %s",
            attempt,
            total_attempts,
            function_app_name,
        )
        time.sleep(delay_seconds)
    return None


def configure_improvement_mcp(
    subscription_id: str,
    rg: str,
    apim_name: str,
    function_app_name: str,
    function_app_rg: str,
    *,
    readiness_attempts: int = 1,
    readiness_delay_seconds: int = _IMPROVEMENT_MCP_READY_DELAY_SECONDS,
    token: str | None = None,
) -> bool:
    """Function App を backend にした improvement-mcp API を APIM へ構成する。"""
    function_details = _wait_for_function_app_mcp_details(
        subscription_id,
        function_app_rg,
        function_app_name,
        attempts=readiness_attempts,
        delay_seconds=readiness_delay_seconds,
        token=token,
    )
    if function_details is None:
        return False

    function_base_url, mcp_extension_key = function_details

    named_value_result = _rest_call(
        _apim_resource_url(subscription_id, rg, apim_name, f"/namedValues/{_IMPROVEMENT_MCP_NAMED_VALUE}"),
        method="PUT",
        body={
            "properties": {
                "displayName": _IMPROVEMENT_MCP_NAMED_VALUE,
                "value": mcp_extension_key,
                "secret": True,
            }
        },
        token=token,
    )
    if named_value_result is None:
        logger.warning("improvement-mcp 用 named value の構成に失敗しました")
        return False

    backend_result = _rest_call(
        _apim_resource_url(subscription_id, rg, apim_name, f"/backends/{_IMPROVEMENT_MCP_BACKEND_ID}"),
        method="PUT",
        body={
            "properties": {
                "url": function_base_url,
                "protocol": "http",
                "credentials": {"header": {"x-functions-key": [f"{{{{{_IMPROVEMENT_MCP_NAMED_VALUE}}}}}"]}},
            }
        },
        token=token,
    )
    if backend_result is None:
        logger.warning("improvement-mcp backend の構成に失敗しました")
        return False

    api_result = _rest_call(
        _apim_resource_url(subscription_id, rg, apim_name, f"/apis/{_IMPROVEMENT_MCP_API_ID}"),
        method="PUT",
        body={
            "properties": {
                "displayName": _IMPROVEMENT_MCP_API_ID,
                "description": "Travel marketing improvement brief MCP server",
                "path": _IMPROVEMENT_MCP_API_ID,
                "protocols": ["https"],
                "subscriptionRequired": False,
                "subscriptionKeyParameterNames": {
                    "header": "Ocp-Apim-Subscription-Key",
                    "query": "subscription-key",
                },
                "backendId": _IMPROVEMENT_MCP_BACKEND_ID,
                "type": "mcp",
                "mcpProperties": {"endpoints": {"mcp": {"uriTemplate": "/runtime/webhooks/mcp"}}},
            }
        },
        token=token,
    )
    if api_result is None:
        logger.warning("improvement-mcp API の構成に失敗しました")
        return False

    policy_result = _rest_call(
        _apim_resource_url(
            subscription_id,
            rg,
            apim_name,
            f"/apis/{_IMPROVEMENT_MCP_API_ID}/policies/policy",
        ),
        method="PUT",
        body={
            "properties": {
                "format": "rawxml",
                "value": _IMPROVEMENT_MCP_POLICY_XML,
            }
        },
        token=token,
    )
    if policy_result is None:
        logger.warning("improvement-mcp policy の構成に失敗しました")
        return False

    logger.info(
        "improvement-mcp を APIM に構成しました: %s -> %s",
        function_app_name,
        function_base_url,
    )
    return True


def apply_ai_gateway_policy(
    subscription_id: str,
    rg: str,
    apim_name: str,
) -> None:
    """APIM の foundry-* API に AI Gateway ポリシーを適用する"""
    token = _get_token()

    # foundry-* API を列挙
    list_url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/resourceGroups/{rg}"
        f"/providers/Microsoft.ApiManagement/service/{apim_name}"
        "/apis?api-version=2024-05-01"
    )
    resp = _rest_call(list_url, token=token)
    if resp is None:
        logger.warning("APIM API 一覧の取得に失敗しました")
        return

    foundry_apis = [api for api in resp.get("value", []) if api["name"].startswith("foundry-")]

    if not foundry_apis:
        logger.info("foundry-* API はまだ作成されていません（後で再実行してください）")
        return

    for api in foundry_apis:
        api_name = api["name"]
        policy_url = (
            f"https://management.azure.com/subscriptions/{subscription_id}"
            f"/resourceGroups/{rg}"
            f"/providers/Microsoft.ApiManagement/service/{apim_name}"
            f"/apis/{api_name}/policies/policy?api-version=2024-05-01"
        )
        body = {
            "properties": {
                "format": "rawxml",
                "value": _AI_GATEWAY_POLICY_XML,
            },
        }
        result = _rest_call(policy_url, method="PUT", body=body, token=token)
        if result is not None:
            logger.info("AI Gateway ポリシーを適用しました: %s", api_name)
        else:
            logger.warning("AI Gateway ポリシー適用に失敗: %s", api_name)


# ---------------------------------------------------------------------------
# Step 3: Entra ID SPA アプリ登録（Voice Live / Work IQ ブラウザ認証用）
# ---------------------------------------------------------------------------

_MICROSOFT_GRAPH_APP_ID = "00000003-0000-0000-c000-000000000000"
_SPA_BROWSER_GRAPH_SCOPE_VALUES = (
    "User.Read",
    "Sites.Read.All",
    "Mail.Read",
    "People.Read.All",
    "OnlineMeetingTranscript.Read.All",
    "Chat.Read",
    "ChannelMessage.Read.All",
    "ExternalItem.Read.All",
)


def _parse_json_stdout(raw: str) -> object:
    """CLI の JSON stdout を安全に解析する。"""
    try:
        return json.loads(raw or "null")
    except json.JSONDecodeError:
        return None


def _patch_graph_application(app_object_id: str, body: dict[str, object]) -> bool:
    """Microsoft Graph application manifest を PATCH する。"""
    url = f"https://graph.microsoft.com/v1.0/applications/{app_object_id}"
    result = _run_cli(
        [
            "az",
            "rest",
            "--method",
            "PATCH",
            "--uri",
            url,
            "--headers",
            "Content-Type=application/json",
            "--body",
            json.dumps(body),
        ],
        capture_output=True,
    )
    if result.returncode == 0:
        return True
    error_text = (result.stderr or result.stdout or "").strip()
    if error_text:
        logger.warning("Graph application PATCH に失敗しました: %s", error_text[:500])
    else:
        logger.warning("Graph application PATCH に失敗しました: returncode=%s", result.returncode)
        return False


def _ensure_spa_redirect_uris(app_id: str, redirect_uris: list[str]) -> None:
    """SPA redirect URI を既存値とマージして同期する。"""
    result = _run_cli(
        ["az", "ad", "app", "show", "--id", app_id, "--query", "{id:id,redirectUris:spa.redirectUris}", "-o", "json"],
        capture_output=True,
    )
    app_details = _parse_json_stdout(result.stdout) if result.returncode == 0 else None
    app_object_id = str(app_details.get("id") or "").strip() if isinstance(app_details, dict) else ""
    existing_redirects = app_details.get("redirectUris") if isinstance(app_details, dict) else None

    merged_redirects: list[str] = []
    for uri in [*(existing_redirects if isinstance(existing_redirects, list) else []), *redirect_uris]:
        normalized = str(uri).strip()
        if normalized and normalized not in merged_redirects:
            merged_redirects.append(normalized)

    if not app_object_id or not merged_redirects:
        return

    if not _patch_graph_application(app_object_id, {"spa": {"redirectUris": merged_redirects}}):
        logger.warning("Entra App redirect URI 更新失敗: %s", app_id)


def _resolve_graph_scope_ids(scope_values: tuple[str, ...]) -> dict[str, str]:
    """Microsoft Graph delegated scope 名から permission ID を解決する。"""
    result = _run_cli(
        [
            "az",
            "ad",
            "sp",
            "show",
            "--id",
            _MICROSOFT_GRAPH_APP_ID,
            "--query",
            "oauth2PermissionScopes[].{value:value,id:id}",
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("Microsoft Graph scope 一覧の取得に失敗しました: %s", result.stderr.strip())
        return {}

    scope_items = _parse_json_stdout(result.stdout)
    available_scope_ids: dict[str, str] = {}
    if isinstance(scope_items, list):
        for item in scope_items:
            if not isinstance(item, dict):
                continue
            value = str(item.get("value") or "").strip()
            scope_id = str(item.get("id") or "").strip()
            if value and scope_id:
                available_scope_ids[value] = scope_id

    missing_scope_values = [scope for scope in scope_values if scope not in available_scope_ids]
    if missing_scope_values:
        logger.warning("Microsoft Graph scope ID を解決できませんでした: %s", ", ".join(missing_scope_values))

    return {scope: available_scope_ids[scope] for scope in scope_values if scope in available_scope_ids}


def _get_existing_graph_scope_ids(app_id: str) -> set[str]:
    """アプリ登録に既に追加済みの Microsoft Graph delegated scope ID を返す。"""
    result = _run_cli(
        [
            "az",
            "ad",
            "app",
            "show",
            "--id",
            app_id,
            "--query",
            f"requiredResourceAccess[?resourceAppId=='{_MICROSOFT_GRAPH_APP_ID}'].resourceAccess[].id",
            "-o",
            "json",
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        logger.warning("Entra App の既存 Graph 権限取得に失敗しました: %s", result.stderr.strip())
        return set()

    raw_scope_ids = _parse_json_stdout(result.stdout)
    if not isinstance(raw_scope_ids, list):
        return set()

    return {str(scope_id).strip() for scope_id in raw_scope_ids if str(scope_id).strip()}


def _ensure_graph_delegated_permissions(app_id: str) -> None:
    """Voice Live / Work IQ で使う Microsoft Graph delegated permissions を同期する。"""
    scope_ids_by_value = _resolve_graph_scope_ids(_SPA_BROWSER_GRAPH_SCOPE_VALUES)
    if not scope_ids_by_value:
        return

    existing_scope_ids = _get_existing_graph_scope_ids(app_id)
    missing_permission_args = [
        f"{scope_id}=Scope"
        for scope_value, scope_id in scope_ids_by_value.items()
        if scope_id not in existing_scope_ids
    ]
    if not missing_permission_args:
        logger.info("Entra App の Graph delegated permissions は既に最新です: %s", app_id)
        return

    add_result = _run_cli(
        [
            "az",
            "ad",
            "app",
            "permission",
            "add",
            "--id",
            app_id,
            "--api",
            _MICROSOFT_GRAPH_APP_ID,
            "--api-permissions",
            *missing_permission_args,
        ],
        capture_output=True,
    )
    if add_result.returncode != 0:
        logger.warning("Entra App の Graph delegated permissions 追加に失敗しました: %s", add_result.stderr.strip())
        return

    logger.info("Entra App の Graph delegated permissions を追加しました: %s", app_id)


def create_entra_app(
    app_name: str = "travel-voice-spa",
    container_app_url: str = "",
) -> str | None:
    """Voice Live / Work IQ 用の Entra ID SPA アプリ登録を作成または再同期する。"""
    result = _run_cli(
        ["az", "ad", "app", "list", "--display-name", app_name, "--query", "[0].appId", "-o", "tsv"],
        capture_output=True,
    )
    app_id = result.stdout.strip()

    if app_id:
        logger.info("Entra App 既存: %s (%s)", app_name, app_id)
    else:
        result = _run_cli(
            [
                "az",
                "ad",
                "app",
                "create",
                "--display-name",
                app_name,
                "--sign-in-audience",
                "AzureADMyOrg",
                "--enable-id-token-issuance",
                "true",
                "--query",
                "appId",
                "-o",
                "tsv",
            ],
            capture_output=True,
        )
        if result.returncode != 0:
            logger.warning("Entra App 作成失敗: %s", result.stderr.strip())
            return None
        app_id = result.stdout.strip()

    redirect_uris = [
        "http://localhost:5173",
        "http://localhost:8000",
        "http://localhost:5173/auth-redirect.html",
        "http://localhost:8000/auth-redirect.html",
    ]
    normalized_container_app_url = container_app_url.strip()
    if normalized_container_app_url:
        redirect_uris.append(normalized_container_app_url)
        redirect_uris.append(f"{normalized_container_app_url.rstrip('/')}/auth-redirect.html")

    _ensure_spa_redirect_uris(app_id, redirect_uris)
    _ensure_graph_delegated_permissions(app_id)

    logger.info("Entra App 構成を同期しました: %s (%s)", app_name, app_id)
    return app_id


# ---------------------------------------------------------------------------
# Step 4: Voice Live 用 Foundry Prompt Agent を作成
# ---------------------------------------------------------------------------


def create_voice_agent(
    project_endpoint: str,
    subscription_id: str,
    rg: str,
) -> bool:
    """Voice Live 用の Foundry Prompt Agent を作成する。"""
    del subscription_id, rg
    agent_name = os.environ.get("VOICE_AGENT_NAME", "travel-voice-orchestrator").strip() or "travel-voice-orchestrator"
    model_name = os.environ.get("MODEL_NAME", "").strip() or os.environ.get("FOUNDRY_MODEL", "").strip() or "gpt-5-4-mini"

    voice_live_config = json.dumps(
        {
            "session": {
                "voice": {
                    "name": "ja-JP-Nanami:DragonHDLatestNeural",
                    "type": "azure-standard",
                    "temperature": 0.8,
                },
                "input_audio_transcription": {
                    "model": "azure-speech",
                },
                "turn_detection": {
                    "type": "azure_semantic_vad",
                    "end_of_utterance_detection": {
                        "model": "semantic_detection_v1_multilingual",
                    },
                },
                "input_audio_noise_reduction": {"type": "azure_deep_noise_suppression"},
                "input_audio_echo_cancellation": {"type": "server_echo_cancellation"},
            }
        },
        ensure_ascii=False,
    )

    metadata: dict[str, str] = {}
    limit = 512
    metadata["microsoft.voice-live.configuration"] = voice_live_config[:limit]
    remaining = voice_live_config[limit:]
    chunk_num = 1
    while remaining:
        metadata[f"microsoft.voice-live.configuration.{chunk_num}"] = remaining[:limit]
        remaining = remaining[limit:]
        chunk_num += 1

    instructions = (
        "あなたは旅行マーケティングのアシスタントです。\n"
        "ユーザーの音声指示を聞き取り、旅行プランの企画を支援します。\n"
        "ユーザーが旅行プランの企画を依頼したら、具体的な旅行先・季節・ターゲット・予算を確認し、\n"
        "企画の方向性を提案してください。\n"
        "日本語で応答してください。"
    )

    project_client: AIProjectClient | None = None
    try:
        project_client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
        try:
            project_client.agents.get(agent_name=agent_name)
            logger.info("Voice Agent 既存: %s", agent_name)
            return True
        except ResourceNotFoundError:
            logger.info("Voice Agent を作成中: %s", agent_name)

        project_client.agents.create_version(
            agent_name=agent_name,
            definition=PromptAgentDefinition(model=model_name, instructions=instructions),
            metadata=metadata,
        )
        logger.info("Voice Agent を作成しました: %s", agent_name)
        return True
    except ClientAuthenticationError as exc:
        logger.warning("Voice Agent の認証に失敗しました: %s", exc)
        return False
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("Voice Agent の作成に失敗しました: %s", exc)
        return False
    except Exception as exc:
        logger.warning("Voice Agent の作成中に予期しないエラーが発生しました: %s", exc)
        return False
    finally:
        close_method = getattr(project_client, "close", None)
        if callable(close_method):
            close_method()


def sync_marketing_plan_agent(project_endpoint: str) -> bool:
    """marketing-plan 用の事前作成済み Foundry Agent を同期する。"""
    from src.foundry_prompt_agents import sync_marketing_plan_agent as sync_agent

    model_name = os.environ.get("MODEL_NAME", "").strip() or os.environ.get("FOUNDRY_MODEL", "").strip() or "gpt-5-4-mini"
    try:
        return sync_agent(project_endpoint, model_name)
    except ClientAuthenticationError as exc:
        logger.warning("marketing-plan Agent の認証に失敗しました: %s", exc)
        return False
    except (ImportError, OSError, RuntimeError, ValueError) as exc:
        logger.warning("marketing-plan Agent の同期に失敗しました: %s", exc)
        return False
    except Exception as exc:
        logger.warning("marketing-plan Agent の同期中に予期しないエラーが発生しました: %s", exc)
        return False


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

_FOUNDRY_API_CREATION_WAIT_SECONDS = 30


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    logger.info("postprovision を開始します")

    # azd 環境変数を読み込み
    azd_env = _get_azd_env()
    env = _merge_env(azd_env)
    subscription_id = azd_env.get("AZURE_SUBSCRIPTION_ID", "") or env.get("AZURE_SUBSCRIPTION_ID", "")
    rg = azd_env.get("AZURE_RESOURCE_GROUP", "") or env.get("AZURE_RESOURCE_GROUP", "")
    apim_name = azd_env.get("AZURE_APIM_NAME", "") or env.get("AZURE_APIM_NAME", "")
    project_endpoint = azd_env.get("AZURE_AI_PROJECT_ENDPOINT", "") or env.get("AZURE_AI_PROJECT_ENDPOINT", "")
    service_web_endpoints = azd_env.get("SERVICE_WEB_ENDPOINTS", "") or env.get("SERVICE_WEB_ENDPOINTS", "")
    container_app_name = azd_env.get("AZURE_CONTAINER_APP_NAME", "") or env.get("AZURE_CONTAINER_APP_NAME", "")

    if not all([subscription_id, rg, project_endpoint]):
        logger.error(
            "必要な環境変数が不足しています "
            "(AZURE_SUBSCRIPTION_ID=%s, AZURE_RESOURCE_GROUP=%s, AZURE_AI_PROJECT_ENDPOINT=%s)",
            bool(subscription_id),
            bool(rg),
            bool(project_endpoint),
        )
        return

    if not apim_name:
        logger.warning("AZURE_APIM_NAME が未設定のため AI Gateway セットアップをスキップします")
        logger.info("postprovision 完了（スキップ）")
        return

    # APIM リソース ID を構築
    apim_resource_id = (
        f"/subscriptions/{subscription_id}/resourceGroups/{rg}/providers/Microsoft.ApiManagement/service/{apim_name}"
    )

    # Step 1: Foundry に AI Gateway 接続を作成
    created = create_ai_gateway_connection(project_endpoint, apim_name, apim_resource_id)

    # Step 2: Foundry が APIM 上に foundry-* API を自動生成するのを待機
    if created:
        logger.info(
            "Foundry が APIM に API を作成するのを待機中 (%d秒)...",
            _FOUNDRY_API_CREATION_WAIT_SECONDS,
        )
        time.sleep(_FOUNDRY_API_CREATION_WAIT_SECONDS)

    # Step 3: AI Gateway ポリシーを適用
    apply_ai_gateway_policy(subscription_id, rg, apim_name)

    # Step 3.5: improvement brief MCP を自動配備し、APIM に登録
    setup_improvement_mcp(subscription_id, rg, apim_name, env)

    # Step 4: Voice Agent 作成
    create_voice_agent(project_endpoint, subscription_id, rg)

    # Step 4.5: marketing-plan Agent 同期
    sync_marketing_plan_agent(project_endpoint)

    # Step 5: Entra App 登録（Voice Live SPA 認証用）
    tenant_result = _run_cli(
        ["az", "account", "show", "--query", "tenantId", "-o", "tsv"],
        capture_output=True,
    )
    tenant_id = tenant_result.stdout.strip()
    container_app_url = service_web_endpoints.strip("[]\"' ")
    app_id = create_entra_app(container_app_url=container_app_url)
    if app_id:
        if _set_azd_env_value("VOICE_SPA_CLIENT_ID", app_id):
            logger.info("Voice SPA Client ID を azd env に保存: %s", app_id)
    if tenant_id:
        if _set_azd_env_value("AZURE_TENANT_ID", tenant_id):
            logger.info("Azure Tenant ID を azd env に保存: %s", tenant_id)

    _update_container_app_env(
        container_app_name,
        rg,
        {
            "VOICE_SPA_CLIENT_ID": app_id or "",
            "AZURE_TENANT_ID": tenant_id,
        },
    )

    logger.info("postprovision 完了")


if __name__ == "__main__":
    main()
