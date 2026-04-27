"""scripts.postprovision のテスト。"""

import json
import shutil
import subprocess
import zipfile
from pathlib import Path

from scripts import postprovision as postprovision_module


def test_derive_improvement_mcp_names_from_container_app() -> None:
    """Container App 名から MCP 用リソース名を安定導出する"""
    function_app_name, storage_account_name = postprovision_module._derive_improvement_mcp_names(
        {"AZURE_CONTAINER_APP_NAME": "ca-abc123"}
    )

    assert function_app_name == "func-mcp-abc123"
    assert storage_account_name == "stfnabc123"


def test_configure_improvement_mcp_registers_named_value_backend_api_and_policy(monkeypatch) -> None:
    """Function App がある場合は APIM の improvement-mcp 一式を構成する"""
    calls: list[dict[str, object]] = []

    def fake_rest_call(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        token: str | None = None,
        scope: str = "https://management.azure.com/.default",
        timeout: int = 30,
    ) -> dict | None:
        del scope, timeout
        calls.append({"url": url, "method": method, "body": body, "token": token})

        if url.endswith("/providers/Microsoft.Web/sites/func-mcp?api-version=2024-04-01") and method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            return {"systemKeys": {"mcp_extension": "secret-key"}}
        return {"ok": True}

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)
    monkeypatch.setattr(postprovision_module, "_wait_for_function_app_mcp_runtime_ready", lambda *args, **kwargs: True)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
        token="prefetched-token",
    )

    assert result is True
    assert all(call["token"] == "prefetched-token" for call in calls)
    assert any("/namedValues/func-mcp-extension-key" in str(call["url"]) for call in calls)
    assert any("/backends/improvement-mcp-backend" in str(call["url"]) for call in calls)
    assert any("/apis/improvement-mcp?" in str(call["url"]) for call in calls)
    assert any("/apis/improvement-mcp/policies/policy" in str(call["url"]) for call in calls)

    backend_call = next(call for call in calls if "/backends/improvement-mcp-backend" in str(call["url"]))
    assert backend_call["body"] == {
        "properties": {
            "url": "https://func-mcp.azurewebsites.net",
            "protocol": "http",
            "credentials": {
                "header": {
                    "x-functions-key": ["{{func-mcp-extension-key}}"],
                }
            },
        }
    }

    api_call = next(call for call in calls if "/apis/improvement-mcp?" in str(call["url"]))
    api_properties = api_call["body"]["properties"]
    assert api_properties["type"] == "mcp"
    assert api_properties["backendId"] == "improvement-mcp-backend"
    assert api_properties["mcpProperties"]["endpoints"]["mcp"]["uriTemplate"] == "/runtime/webhooks/mcp"


def test_configure_improvement_mcp_retries_until_mcp_extension_key_is_available(monkeypatch) -> None:
    """配備直後の遅延で system key が未作成でもリトライで回復する"""
    calls: list[dict[str, object]] = []
    key_attempts = 0

    def fake_rest_call(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        token: str | None = None,
        scope: str = "https://management.azure.com/.default",
        timeout: int = 30,
    ) -> dict | None:
        nonlocal key_attempts
        del token, scope, timeout
        calls.append({"url": url, "method": method, "body": body})

        if url.endswith("/providers/Microsoft.Web/sites/func-mcp?api-version=2024-04-01") and method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            key_attempts += 1
            if key_attempts < 3:
                return {"systemKeys": {}}
            return {"systemKeys": {"mcp_extension": "secret-key"}}
        return {"ok": True}

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)
    monkeypatch.setattr(postprovision_module, "_wait_for_function_app_mcp_runtime_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module.time, "sleep", lambda _seconds: None)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
        readiness_attempts=3,
        readiness_delay_seconds=0,
    )

    assert result is True
    assert key_attempts == 3
    assert any("/apis/improvement-mcp?" in str(call["url"]) for call in calls)


def test_configure_improvement_mcp_returns_false_when_mcp_extension_key_is_missing(monkeypatch) -> None:
    """mcp_extension key が取得できない場合は APIM 登録を中断する"""
    calls: list[dict[str, object]] = []

    def fake_rest_call(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        token: str | None = None,
        scope: str = "https://management.azure.com/.default",
        timeout: int = 30,
    ) -> dict | None:
        del body, token, scope, timeout
        calls.append({"url": url, "method": method})
        if method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            return {"systemKeys": {}}
        return {"ok": True}

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)
    monkeypatch.setattr(postprovision_module.time, "sleep", lambda _seconds: None)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
        readiness_attempts=2,
        readiness_delay_seconds=0,
    )

    assert result is False
    assert len(calls) == 4


def test_configure_improvement_mcp_waits_for_runtime_before_apim_registration(monkeypatch) -> None:
    """APIM 登録前に MCP runtime の応答準備を待つ。"""
    calls: list[dict[str, object]] = []
    readiness_calls: list[dict[str, object]] = []

    def fake_rest_call(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        token: str | None = None,
        scope: str = "https://management.azure.com/.default",
        timeout: int = 30,
    ) -> dict | None:
        del scope, timeout
        calls.append({"url": url, "method": method, "body": body, "token": token})

        if url.endswith("/providers/Microsoft.Web/sites/func-mcp?api-version=2024-04-01") and method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            return {"systemKeys": {"mcp_extension": "secret-key"}}
        return {"ok": True}

    def fake_wait_for_runtime(
        function_base_url: str,
        mcp_extension_key: str,
        attempts: int,
        delay_seconds: int,
    ) -> bool:
        readiness_calls.append(
            {
                "function_base_url": function_base_url,
                "mcp_extension_key": mcp_extension_key,
                "attempts": attempts,
                "delay_seconds": delay_seconds,
            }
        )
        return True

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)
    monkeypatch.setattr(postprovision_module, "_wait_for_function_app_mcp_runtime_ready", fake_wait_for_runtime)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
        readiness_attempts=4,
        readiness_delay_seconds=1,
        token="prefetched-token",
    )

    assert result is True
    assert readiness_calls == [
        {
            "function_base_url": "https://func-mcp.azurewebsites.net",
            "mcp_extension_key": "secret-key",
            "attempts": 4,
            "delay_seconds": 1,
        }
    ]
    assert any("/apis/improvement-mcp?" in str(call["url"]) for call in calls)


def test_configure_improvement_mcp_returns_false_when_runtime_not_ready(monkeypatch) -> None:
    """runtime endpoint が未準備なら APIM 登録を中断する。"""
    calls: list[dict[str, object]] = []

    def fake_rest_call(
        url: str,
        *,
        method: str = "GET",
        body: dict | None = None,
        token: str | None = None,
        scope: str = "https://management.azure.com/.default",
        timeout: int = 30,
    ) -> dict | None:
        del body, token, scope, timeout
        calls.append({"url": url, "method": method})
        if method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            return {"systemKeys": {"mcp_extension": "secret-key"}}
        return {"ok": True}

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)
    monkeypatch.setattr(postprovision_module, "_wait_for_function_app_mcp_runtime_ready", lambda *args, **kwargs: False)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
        readiness_attempts=2,
        readiness_delay_seconds=0,
    )

    assert result is False
    assert all("/namedValues/" not in str(call["url"]) for call in calls)


def test_build_mcp_package_creates_ready_to_run_zip_with_vendored_dependencies(monkeypatch) -> None:
    """ready-to-run zip には vendored site-packages を含める。"""
    build_root = Path(__file__).resolve().parent / ".artifacts-postprovision-build"

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        target_index = command.index("--target") + 1
        vendor_root = Path(command[target_index])
        dependency_file = vendor_root / "azure" / "functions" / "__init__.py"
        dependency_file.parent.mkdir(parents=True, exist_ok=True)
        dependency_file.write_text("# vendored dependency\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postprovision_module, "_IMPROVEMENT_MCP_BUILD_ROOT", build_root)
    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)

    package_path = postprovision_module._build_mcp_package()

    try:
        assert package_path is not None
        assert package_path == build_root / postprovision_module._IMPROVEMENT_MCP_PACKAGE_NAME
        assert package_path.exists()
        with zipfile.ZipFile(package_path) as archive:
            archived_files = set(archive.namelist())
        assert "function_app.py" in archived_files
        assert ".python_packages/lib/site-packages/azure/functions/__init__.py" in archived_files
    finally:
        if build_root.exists():
            shutil.rmtree(build_root)


def test_deploy_improvement_mcp_function_uses_local_zip_without_remote_build(monkeypatch) -> None:
    """配備コマンドは local build zip をそのまま config-zip へ渡す。"""
    build_root = Path(__file__).resolve().parent / ".artifacts-postprovision-deploy"
    package_path = build_root / postprovision_module._IMPROVEMENT_MCP_PACKAGE_NAME
    commands: list[list[str]] = []

    build_root.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(b"zip")

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(postprovision_module, "ensure_storage_account", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_function_app", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_managed_identity_storage", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "_build_mcp_package", lambda: package_path)
    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)

    result = postprovision_module.deploy_improvement_mcp_function(
        resource_group="rg-dev",
        location="eastus2",
        function_app_name="func-mcp-abc123",
        storage_account_name="stfnabc123",
    )

    assert result is True
    deploy_command = commands[0]
    assert deploy_command[:5] == ["az", "functionapp", "deployment", "source", "config-zip"]
    assert deploy_command[deploy_command.index("--build-remote") + 1] == "false"
    assert build_root.exists() is False


def test_deploy_improvement_mcp_function_accepts_partial_zip_deploy_success(monkeypatch) -> None:
    """zip 配備が partial success でも Kudu upload/sync まで進んでいれば継続する。"""
    build_root = Path(__file__).resolve().parent / ".artifacts-postprovision-partial"
    package_path = build_root / postprovision_module._IMPROVEMENT_MCP_PACKAGE_NAME

    build_root.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(b"zip")

    partial_success_stderr = "\n".join(
        [
            "WARNING: Getting scm site credentials for zip deployment",
            "WARNING: Starting zip deployment. This operation can take a while to complete ...",
            'ERROR: Deployment was partially successful. These are the deployment logs:',
            '[Kudu-UploadPackageStep] completed. Uploaded package to storage successfully.',
            '[Kudu-SyncTriggerStep] starting.',
        ]
    )

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=partial_success_stderr)

    monkeypatch.setattr(postprovision_module, "ensure_storage_account", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_function_app", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_managed_identity_storage", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "_build_mcp_package", lambda: package_path)
    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)

    result = postprovision_module.deploy_improvement_mcp_function(
        resource_group="rg-dev",
        location="eastus2",
        function_app_name="func-mcp-abc123",
        storage_account_name="stfnabc123",
    )

    assert result is True
    assert build_root.exists() is False


def test_deploy_improvement_mcp_function_accepts_flex_async_zip_deploy(monkeypatch) -> None:
    """Flex Consumption の 202 partial success は readiness wait に委ねる。"""
    build_root = Path(__file__).resolve().parent / ".artifacts-postprovision-flex-partial"
    package_path = build_root / postprovision_module._IMPROVEMENT_MCP_PACKAGE_NAME

    build_root.mkdir(parents=True, exist_ok=True)
    package_path.write_bytes(b"zip")

    partial_success_stderr = "\n".join(
        [
            "WARNING: Getting scm site credentials for zip deployment",
            "WARNING: Starting zip deployment. This operation can take a while to complete ...",
            'WARNING: Deployment endpoint responded with status code 202 for deployment id "abc"',
            "ERROR: Deployment was partially successful. These are the deployment logs:",
            "The logs you are looking for were not found. In flex consumption plans, "
            "the instance will be recycled and logs will not be persisted after that.",
        ]
    )

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        return subprocess.CompletedProcess(command, 1, stdout="", stderr=partial_success_stderr)

    monkeypatch.setattr(postprovision_module, "ensure_storage_account", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_function_app", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "ensure_improvement_mcp_managed_identity_storage", lambda *args, **kwargs: True)
    monkeypatch.setattr(postprovision_module, "_build_mcp_package", lambda: package_path)
    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)

    result = postprovision_module.deploy_improvement_mcp_function(
        resource_group="rg-dev",
        location="eastus2",
        function_app_name="func-mcp-abc123",
        storage_account_name="stfnabc123",
    )

    assert result is True
    assert build_root.exists() is False


def test_setup_improvement_mcp_deploys_and_configures(monkeypatch) -> None:
    """setup_improvement_mcp は配備後に APIM 登録まで実行する"""
    captured: dict[str, object] = {}

    def fake_deploy_improvement_mcp_function(
        resource_group: str,
        location: str,
        function_app_name: str,
        storage_account_name: str,
    ) -> bool:
        captured["deploy"] = {
            "resource_group": resource_group,
            "location": location,
            "function_app_name": function_app_name,
            "storage_account_name": storage_account_name,
        }
        return True

    def fake_configure_improvement_mcp(
        subscription_id: str,
        rg: str,
        apim_name: str,
        function_app_name: str,
        function_app_rg: str,
        *,
        readiness_attempts: int = 1,
        readiness_delay_seconds: int = postprovision_module._IMPROVEMENT_MCP_READY_DELAY_SECONDS,
        token: str | None = None,
    ) -> bool:
        captured["configure"] = {
            "subscription_id": subscription_id,
            "rg": rg,
            "apim_name": apim_name,
            "function_app_name": function_app_name,
            "function_app_rg": function_app_rg,
            "readiness_attempts": readiness_attempts,
            "readiness_delay_seconds": readiness_delay_seconds,
            "token": token,
        }
        return True

    monkeypatch.setattr(postprovision_module, "deploy_improvement_mcp_function", fake_deploy_improvement_mcp_function)
    monkeypatch.setattr(postprovision_module, "configure_improvement_mcp", fake_configure_improvement_mcp)
    monkeypatch.setattr(
        postprovision_module, "_get_token", lambda scope="https://management.azure.com/.default": "prefetched-token"
    )
    monkeypatch.setattr(
        postprovision_module, "_resolve_resource_group_location", lambda rg, configured_location="": "eastus2"
    )
    monkeypatch.setattr(postprovision_module, "_sync_improvement_mcp_env", lambda *args: None)

    result = postprovision_module.setup_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        env={"AZURE_CONTAINER_APP_NAME": "ca-abc123"},
    )

    assert result is True
    assert captured["deploy"] == {
        "resource_group": "rg-dev",
        "location": "eastus2",
        "function_app_name": "func-mcp-abc123",
        "storage_account_name": "stfnabc123",
    }
    assert captured["configure"] == {
        "subscription_id": "sub-id",
        "rg": "rg-dev",
        "apim_name": "apim-test",
        "function_app_name": "func-mcp-abc123",
        "function_app_rg": "rg-dev",
        "readiness_attempts": postprovision_module._IMPROVEMENT_MCP_READY_ATTEMPTS,
        "readiness_delay_seconds": postprovision_module._IMPROVEMENT_MCP_READY_DELAY_SECONDS,
        "token": "prefetched-token",
    }


def test_setup_improvement_mcp_returns_false_when_deploy_fails(monkeypatch) -> None:
    """配備が失敗した場合は APIM 登録を行わず失敗として返す"""
    configure_called = False

    def fake_configure_improvement_mcp(*args, **kwargs) -> bool:
        nonlocal configure_called
        del args, kwargs
        configure_called = True
        return True

    monkeypatch.setattr(postprovision_module, "deploy_improvement_mcp_function", lambda **kwargs: False)
    monkeypatch.setattr(postprovision_module, "configure_improvement_mcp", fake_configure_improvement_mcp)
    monkeypatch.setattr(
        postprovision_module, "_get_token", lambda scope="https://management.azure.com/.default": "prefetched-token"
    )
    monkeypatch.setattr(
        postprovision_module, "_resolve_resource_group_location", lambda rg, configured_location="": "eastus2"
    )

    result = postprovision_module.setup_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        env={"AZURE_CONTAINER_APP_NAME": "ca-abc123"},
    )

    assert result is False
    assert configure_called is False


def test_ensure_storage_account_updates_existing_storage_network_settings(monkeypatch) -> None:
    """既存 storage account でも Flex 用のネットワーク設定へ揃える"""
    commands: list[list[str]] = []

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        if command[:4] == ["az", "storage", "account", "show"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"name": "stfnabc123"}), stderr="")
        if command[:4] == ["az", "storage", "account", "update"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"name": "stfnabc123"}), stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)

    result = postprovision_module.ensure_storage_account(
        resource_group="rg-dev",
        location="eastus2",
        storage_account_name="stfnabc123",
    )

    assert result is True
    assert any(
        command[:4] == ["az", "storage", "account", "update"] and "Enabled" in command for command in commands
    )


def test_ensure_improvement_mcp_managed_identity_storage_switches_to_system_identity(monkeypatch) -> None:
    """Function App の storage 認証を system assigned managed identity へ切り替える"""
    commands: list[list[str]] = []

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)

        if command[:4] == ["az", "functionapp", "identity", "assign"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"principalId": "principal-1"}), stderr="")
        if command[:4] == ["az", "storage", "account", "show"] and "--query" in command:
            return subprocess.CompletedProcess(command, 0, stdout="/storage/id", stderr="")
        if command[:4] == ["az", "role", "assignment", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:4] == ["az", "role", "assignment", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"id": "role-id"}), stderr="")
        if command[:4] == ["az", "functionapp", "deployment", "config"] and "show" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps(
                    {
                        "storage": {
                            "value": "https://storage.blob.core.windows.net/app-package-funcmcpabc123-1234567"
                        }
                    }
                ),
                stderr="",
            )
        if command[:4] == ["az", "functionapp", "config", "appsettings"]:
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:4] == ["az", "functionapp", "deployment", "config"] and "set" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"ok": True}), stderr="")
        if command[:3] == ["az", "functionapp", "restart"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)
    monkeypatch.setattr(postprovision_module.time, "sleep", lambda _seconds: None)

    result = postprovision_module.ensure_improvement_mcp_managed_identity_storage(
        resource_group="rg-dev",
        function_app_name="func-mcp-abc123",
        storage_account_name="stfnabc123",
    )

    assert result is True
    assert any(
        command[:4] == ["az", "functionapp", "identity", "assign"] and "--name" in command and "func-mcp-abc123" in command
        for command in commands
    )
    assert any(
        command[:4] == ["az", "role", "assignment", "create"]
        and postprovision_module._STORAGE_BLOB_DATA_CONTRIBUTOR_ROLE in command
        for command in commands
    )
    assert any(
        command[:4] == ["az", "role", "assignment", "create"]
        and postprovision_module._STORAGE_QUEUE_DATA_CONTRIBUTOR_ROLE in command
        for command in commands
    )
    assert any(
        command[:4] == ["az", "role", "assignment", "create"]
        and postprovision_module._STORAGE_QUEUE_DATA_MESSAGE_PROCESSOR_ROLE in command
        for command in commands
    )
    assert any(
        command[:4] == ["az", "functionapp", "deployment", "config"] and "set" in command and "SystemAssignedIdentity" in command
        for command in commands
    )
    assert any(
        command[:4] == ["az", "functionapp", "config", "appsettings"] and "AzureWebJobsStorage__accountName=stfnabc123" in command
        for command in commands
    )
    assert any(command[:3] == ["az", "functionapp", "restart"] for command in commands)


def test_create_voice_agent_creates_agent_when_missing(monkeypatch) -> None:
    """Voice Agent が未作成なら SDK で create_version を呼ぶ"""

    class FakeNotFoundError(Exception):
        """ResourceNotFoundError の代替。"""

    class _FakeAgents:
        def __init__(self) -> None:
            self.create_calls: list[dict[str, object]] = []

        def get(self, *, agent_name: str):
            assert agent_name == "travel-voice-orchestrator"
            raise FakeNotFoundError("missing")

        def create_version(self, *, agent_name: str, definition: dict[str, str], metadata: dict[str, str]):
            self.create_calls.append(
                {
                    "agent_name": agent_name,
                    "definition": definition,
                    "metadata": metadata,
                }
            )
            return {"name": agent_name, "version": "1"}

    class _FakeProjectClient:
        def __init__(self) -> None:
            self.agents = _FakeAgents()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_client = _FakeProjectClient()

    monkeypatch.setattr(postprovision_module, "ResourceNotFoundError", FakeNotFoundError)
    monkeypatch.setattr(postprovision_module, "AIProjectClient", lambda endpoint, credential: fake_client)
    monkeypatch.setattr(postprovision_module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(
        postprovision_module,
        "PromptAgentDefinition",
        lambda model, instructions: {"model": model, "instructions": instructions},
    )
    monkeypatch.delenv("VOICE_AGENT_NAME", raising=False)
    monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")

    result = postprovision_module.create_voice_agent(
        project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        subscription_id="sub-id",
        rg="rg-dev",
    )

    assert result is True
    assert fake_client.closed is True
    assert fake_client.agents.create_calls == [
        {
            "agent_name": "travel-voice-orchestrator",
            "definition": {
                "model": "gpt-5-4-mini",
                "instructions": (
                    "あなたは旅行マーケティングのアシスタントです。\n"
                    "ユーザーの音声指示を聞き取り、旅行プランの企画を支援します。\n"
                    "ユーザーが旅行プランの企画を依頼したら、具体的な旅行先・季節・ターゲット・予算を確認し、\n"
                    "企画の方向性を提案してください。\n"
                    "日本語で応答してください。"
                ),
            },
            "metadata": fake_client.agents.create_calls[0]["metadata"],
        }
    ]
    metadata = fake_client.agents.create_calls[0]["metadata"]
    assert metadata["microsoft.voice-live.configuration"]
    assert "semantic_detection_v1_multilingual" in "".join(metadata.values())


def test_sync_marketing_plan_agent_uses_foundry_helper(monkeypatch) -> None:
    """marketing-plan Agent 同期は共通 helper に委譲する。"""

    captured: list[tuple[str, str]] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        captured.append((project_endpoint, model_name))
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")
    monkeypatch.delenv("ENABLE_GPT_55", raising=False)
    monkeypatch.delenv("GPT_55_AVAILABLE", raising=False)
    monkeypatch.delenv("GPT_55_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("GPT_5_5_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENDPOINT", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is True
    assert captured == [
        ("https://example.test", "gpt-5-4-mini"),
        ("https://example.test", "gpt-5.4"),
        ("https://example.test", "gpt-4-1-mini"),
        ("https://example.test", "gpt-4.1"),
    ]


def test_sync_marketing_plan_agent_includes_requested_model_once(monkeypatch) -> None:
    """UI 既定外の requested model も重複なく同期する。"""

    captured: list[str] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        assert project_endpoint == "https://example.test"
        captured.append(model_name)
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5.4-turbo")
    monkeypatch.delenv("ENABLE_GPT_55", raising=False)
    monkeypatch.delenv("GPT_55_AVAILABLE", raising=False)
    monkeypatch.delenv("GPT_55_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("GPT_5_5_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENDPOINT", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is True
    assert captured == [
        "gpt-5.4-turbo",
        "gpt-5-4-mini",
        "gpt-5.4",
        "gpt-4-1-mini",
        "gpt-4.1",
    ]


def test_sync_marketing_plan_agent_skips_optional_gpt55_when_not_deployed(monkeypatch) -> None:
    """optional な GPT-5.5 agent sync 失敗は既存 deployment の postprovision を壊さない。"""

    captured: list[str] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        assert project_endpoint == "https://example.test"
        captured.append(model_name)
        if model_name == "gpt-5.5":
            raise ValueError("model deployment not found")
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")
    monkeypatch.setenv("ENABLE_GPT_55", "true")
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENDPOINT", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is True
    assert captured == [
        "gpt-5-4-mini",
        "gpt-5.4",
        "gpt-4-1-mini",
        "gpt-4.1",
        "gpt-5.5",
    ]


def test_sync_marketing_plan_agent_skips_optional_gpt55_azure_http_error(monkeypatch) -> None:
    """Azure SDK 由来の未デプロイ応答でも optional GPT-5.5 は既存同期を壊さない。"""

    captured: list[str] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        assert project_endpoint == "https://example.test"
        captured.append(model_name)
        if model_name == "gpt-5.5":
            raise postprovision_module.HttpResponseError(message="Deployment not found")
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")
    monkeypatch.setenv("ENABLE_GPT_55", "true")
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENABLED", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_MODEL_NAME", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_ENDPOINT", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is True
    assert captured == [
        "gpt-5-4-mini",
        "gpt-5.4",
        "gpt-4-1-mini",
        "gpt-4.1",
        "gpt-5.5",
    ]


def test_sync_marketing_plan_agent_fails_when_requested_gpt55_is_not_deployed(monkeypatch) -> None:
    """明示選択された GPT-5.5 は optional 扱いせず、未デプロイなら postprovision 失敗にする。"""

    captured: list[str] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        assert project_endpoint == "https://example.test"
        captured.append(model_name)
        if model_name == "gpt-5.5":
            raise ValueError("model deployment not found")
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5.5")
    monkeypatch.delenv("ENABLE_MODEL_ROUTER", raising=False)
    monkeypatch.delenv("MODEL_ROUTER_DEPLOYMENT_NAME", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is False
    assert captured == ["gpt-5.5"]


def test_sync_marketing_plan_agent_includes_model_router_only_when_enabled(monkeypatch) -> None:
    """Model Router agent は明示設定時だけ optional 同期対象にする。"""

    captured: list[str] = []

    def fake_sync(project_endpoint: str, model_name: str) -> bool:
        assert project_endpoint == "https://example.test"
        captured.append(model_name)
        return True

    monkeypatch.setenv("MODEL_NAME", "gpt-5-4-mini")
    monkeypatch.setenv("ENABLE_MODEL_ROUTER", "true")
    monkeypatch.setenv("MODEL_ROUTER_DEPLOYMENT_NAME", "router-prod")
    monkeypatch.delenv("ENABLE_GPT_55", raising=False)
    monkeypatch.delenv("GPT_55_AVAILABLE", raising=False)
    monkeypatch.delenv("GPT_55_DEPLOYMENT_NAME", raising=False)
    monkeypatch.delenv("GPT_5_5_DEPLOYMENT_NAME", raising=False)
    monkeypatch.setattr("src.foundry_prompt_agents.sync_marketing_plan_agent", fake_sync)

    result = postprovision_module.sync_marketing_plan_agent("https://example.test")

    assert result is True
    assert captured == [
        "gpt-5-4-mini",
        "gpt-5.4",
        "gpt-4-1-mini",
        "gpt-4.1",
        "router-prod",
    ]


def test_create_voice_agent_returns_true_when_agent_already_exists(monkeypatch) -> None:
    """既存 Voice Agent があれば新規作成しない"""

    class _FakeAgents:
        def __init__(self) -> None:
            self.create_called = False

        def get(self, *, agent_name: str):
            assert agent_name == "travel-voice-orchestrator"
            return {"name": agent_name}

        def create_version(self, **kwargs):
            del kwargs
            self.create_called = True
            raise AssertionError("create_version should not be called")

    class _FakeProjectClient:
        def __init__(self) -> None:
            self.agents = _FakeAgents()

        def close(self) -> None:
            return None

    fake_client = _FakeProjectClient()

    monkeypatch.setattr(postprovision_module, "AIProjectClient", lambda endpoint, credential: fake_client)
    monkeypatch.setattr(postprovision_module, "DefaultAzureCredential", lambda: object())

    result = postprovision_module.create_voice_agent(
        project_endpoint="https://example.services.ai.azure.com/api/projects/demo",
        subscription_id="sub-id",
        rg="rg-dev",
    )

    assert result is True
    assert fake_client.agents.create_called is False


def test_create_entra_app_reconciles_existing_app_redirects_and_graph_permissions(monkeypatch) -> None:
    """既存 SPA アプリでも redirect URI と Work IQ 用 Graph 権限を追補する"""
    commands: list[list[str]] = []
    patched_applications: list[dict[str, object]] = []

    graph_scope_rows = [
        {"value": "User.Read", "id": "scope-user-read"},
        {"value": "Sites.Read.All", "id": "scope-sites-read"},
        {"value": "Mail.Read", "id": "scope-mail-read"},
        {"value": "People.Read.All", "id": "scope-people-read"},
        {"value": "OnlineMeetingTranscript.Read.All", "id": "scope-meeting-read"},
        {"value": "Chat.Read", "id": "scope-chat-read"},
        {"value": "ChannelMessage.Read.All", "id": "scope-channel-read"},
        {"value": "ExternalItem.Read.All", "id": "scope-external-read"},
    ]

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)

        if command[:4] == ["az", "ad", "app", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="existing-app-id\n", stderr="")
        if command[:4] == ["az", "ad", "app", "show"] and "{id:id,redirectUris:spa.redirectUris}" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"id": "existing-object-id", "redirectUris": ["http://localhost:5173"]}),
                stderr="",
            )
        if command[:4] == ["az", "ad", "sp", "show"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(graph_scope_rows), stderr="")
        if command[:4] == ["az", "ad", "app", "show"] and any(
            "requiredResourceAccess" in part for part in command
        ):
            return subprocess.CompletedProcess(command, 0, stdout='["scope-user-read"]', stderr="")
        if command[:5] == ["az", "ad", "app", "permission", "add"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)
    monkeypatch.setattr(
        postprovision_module,
        "_patch_graph_application",
        lambda app_object_id, body: patched_applications.append({"app_object_id": app_object_id, "body": body}) or True,
    )

    app_id = postprovision_module.create_entra_app(container_app_url="https://example.contoso.com")

    assert app_id == "existing-app-id"
    assert patched_applications == [
        {
            "app_object_id": "existing-object-id",
            "body": {
                "spa": {
                    "redirectUris": [
                        "http://localhost:5173",
                        "http://localhost:8000",
                        "http://localhost:5173/auth-redirect.html",
                        "http://localhost:8000/auth-redirect.html",
                        "https://example.contoso.com",
                        "https://example.contoso.com/auth-redirect.html",
                    ]
                }
            },
        }
    ]

    permission_add_command = next(command for command in commands if command[:5] == ["az", "ad", "app", "permission", "add"])
    assert permission_add_command == [
        "az",
        "ad",
        "app",
        "permission",
        "add",
        "--id",
        "existing-app-id",
        "--api",
        postprovision_module._MICROSOFT_GRAPH_APP_ID,
        "--api-permissions",
        "scope-sites-read=Scope",
        "scope-mail-read=Scope",
        "scope-people-read=Scope",
        "scope-meeting-read=Scope",
        "scope-chat-read=Scope",
        "scope-channel-read=Scope",
        "scope-external-read=Scope",
    ]


def test_create_entra_app_creates_app_when_missing(monkeypatch) -> None:
    """SPA アプリが未作成なら作成後に redirect URI と Graph 権限を同期する"""
    commands: list[list[str]] = []
    patched_applications: list[dict[str, object]] = []

    graph_scope_rows = [
        {"value": scope_value, "id": f"scope-{index}"}
        for index, scope_value in enumerate(postprovision_module._SPA_BROWSER_GRAPH_SCOPE_VALUES, start=1)
    ]

    def fake_run_cli(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)

        if command[:4] == ["az", "ad", "app", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[:4] == ["az", "ad", "app", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="new-app-id\n", stderr="")
        if command[:4] == ["az", "ad", "app", "show"] and "{id:id,redirectUris:spa.redirectUris}" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=json.dumps({"id": "new-object-id", "redirectUris": []}),
                stderr="",
            )
        if command[:4] == ["az", "ad", "sp", "show"]:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(graph_scope_rows), stderr="")
        if command[:4] == ["az", "ad", "app", "show"] and any(
            "requiredResourceAccess" in part for part in command
        ):
            return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
        if command[:5] == ["az", "ad", "app", "permission", "add"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(postprovision_module, "_run_cli", fake_run_cli)
    monkeypatch.setattr(
        postprovision_module,
        "_patch_graph_application",
        lambda app_object_id, body: patched_applications.append({"app_object_id": app_object_id, "body": body}) or True,
    )

    app_id = postprovision_module.create_entra_app(container_app_url="https://example.contoso.com")

    assert app_id == "new-app-id"
    assert any(command[:4] == ["az", "ad", "app", "create"] for command in commands)
    assert patched_applications == [
        {
            "app_object_id": "new-object-id",
            "body": {
                "spa": {
                    "redirectUris": [
                        "http://localhost:5173",
                        "http://localhost:8000",
                        "http://localhost:5173/auth-redirect.html",
                        "http://localhost:8000/auth-redirect.html",
                        "https://example.contoso.com",
                        "https://example.contoso.com/auth-redirect.html",
                    ]
                }
            },
        }
    ]

    permission_add_command = next(command for command in commands if command[:5] == ["az", "ad", "app", "permission", "add"])
    assert permission_add_command[-len(postprovision_module._SPA_BROWSER_GRAPH_SCOPE_VALUES):] == [
        "scope-1=Scope",
        "scope-2=Scope",
        "scope-3=Scope",
        "scope-4=Scope",
        "scope-5=Scope",
        "scope-6=Scope",
        "scope-7=Scope",
        "scope-8=Scope",
    ]
