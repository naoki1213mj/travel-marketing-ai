"""scripts.postprovision のテスト。"""

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
        del token, scope, timeout
        calls.append({"url": url, "method": method, "body": body})

        if url.endswith("/providers/Microsoft.Web/sites/func-mcp?api-version=2024-04-01") and method == "GET":
            return {"properties": {"defaultHostName": "func-mcp.azurewebsites.net"}}
        if "/host/default/listKeys" in url and method == "POST":
            return {"systemKeys": {"mcp_extension": "secret-key"}}
        return {"ok": True}

    monkeypatch.setattr(postprovision_module, "_rest_call", fake_rest_call)

    result = postprovision_module.configure_improvement_mcp(
        subscription_id="sub-id",
        rg="rg-dev",
        apim_name="apim-test",
        function_app_name="func-mcp",
        function_app_rg="rg-dev",
    )

    assert result is True
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
    ) -> bool:
        captured["configure"] = {
            "subscription_id": subscription_id,
            "rg": rg,
            "apim_name": apim_name,
            "function_app_name": function_app_name,
            "function_app_rg": function_app_rg,
            "readiness_attempts": readiness_attempts,
            "readiness_delay_seconds": readiness_delay_seconds,
        }
        return True

    monkeypatch.setattr(postprovision_module, "deploy_improvement_mcp_function", fake_deploy_improvement_mcp_function)
    monkeypatch.setattr(postprovision_module, "configure_improvement_mcp", fake_configure_improvement_mcp)
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
    }
