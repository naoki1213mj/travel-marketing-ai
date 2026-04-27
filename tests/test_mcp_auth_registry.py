"""mcp_auth_registry の安全ポリシーテスト。"""

import pytest

from src.mcp_auth_registry import (
    McpAccessMode,
    McpApprovalPolicy,
    McpAuthConfig,
    McpAuthMode,
    McpLeastPrivilegeMetadata,
    McpServerRegistryEntry,
    build_improvement_mcp_registry_entry,
    build_mcp_auth_headers,
    decide_mcp_tool_policy,
    mcp_registry_telemetry,
    validate_mcp_registry_entry,
)


def _entry(**overrides: object) -> McpServerRegistryEntry:
    """検証用 registry entry を返す。"""
    base = {
        "server_id": "demo-mcp",
        "display_name": "Demo MCP",
        "endpoint": "https://mcp.example.com/runtime/webhooks/mcp",
        "allowed_hosts": ("mcp.example.com",),
        "allowed_tools": ("search_records", "update_record"),
        "auth": McpAuthConfig(
            mode=McpAuthMode.DELEGATED_BEARER,
            delegated_audience="https://mcp.example.com/.default",
        ),
        "access_mode": McpAccessMode.READ_WRITE,
        "approval_policy": McpApprovalPolicy.REQUIRE_FOR_WRITES,
        "write_tools": ("update_record",),
        "least_privilege": McpLeastPrivilegeMetadata(
            purpose="Search and update approved demo records.",
            data_classification="internal",
            allowed_operations=("search_records", "update_record"),
            required_scopes=("Records.ReadWrite",),
            owner="travel-marketing-backend",
        ),
    }
    base.update(overrides)
    return McpServerRegistryEntry(**base)  # type: ignore[arg-type]


def test_validate_mcp_registry_rejects_unallowlisted_host() -> None:
    """endpoint host は allowlist と一致しないと無効。"""
    entry = _entry(endpoint="https://evil.example.com/mcp")

    errors = validate_mcp_registry_entry(entry)

    assert "host_not_allowed" in errors


def test_validate_mcp_registry_enforces_auth_and_metadata() -> None:
    """認証方式と最小権限 metadata の必須項目を検証する。"""
    entry = _entry(
        auth=McpAuthConfig(mode=McpAuthMode.NONE),
        least_privilege=McpLeastPrivilegeMetadata(
            purpose="",
            data_classification="",
            allowed_operations=("unknown_tool",),
        ),
    )

    errors = validate_mcp_registry_entry(entry)

    assert "none_auth_requires_read_only" in errors
    assert "least_privilege_purpose_required" in errors
    assert "least_privilege_data_classification_required" in errors
    assert "least_privilege_operation_not_allowed:unknown_tool" in errors


def test_decide_mcp_tool_policy_allows_reads_and_requires_write_approval() -> None:
    """許可 tool の read は通し、write は承認待ちにする。"""
    entry = _entry()

    read_decision = decide_mcp_tool_policy(entry, "search_records")
    write_decision = decide_mcp_tool_policy(entry, "update_record", operation="write")
    approved_decision = decide_mcp_tool_policy(entry, "update_record", operation="write", approval_granted=True)

    assert read_decision.allowed is True
    assert read_decision.reason == "allowed_read"
    assert write_decision.allowed is False
    assert write_decision.approval_required is True
    assert write_decision.reason == "approval_required"
    assert approved_decision.allowed is True
    assert approved_decision.reason == "allowed_write"


def test_decide_mcp_tool_policy_denies_unknown_and_read_only_writes() -> None:
    """未許可 tool と read-only mode の mutation は拒否する。"""
    read_only_entry = _entry(
        auth=McpAuthConfig(mode=McpAuthMode.NONE),
        access_mode=McpAccessMode.READ_ONLY,
        approval_policy=McpApprovalPolicy.DENY_WRITES,
        least_privilege=McpLeastPrivilegeMetadata(
            purpose="Read demo records.",
            data_classification="internal",
            allowed_operations=("search_records", "update_record"),
        ),
    )

    unknown_decision = decide_mcp_tool_policy(read_only_entry, "delete_record")
    write_decision = decide_mcp_tool_policy(read_only_entry, "update_record", operation="mutation")

    assert unknown_decision.allowed is False
    assert unknown_decision.reason == "tool_not_allowed"
    assert write_decision.allowed is False
    assert write_decision.reason == "read_only_mode"


def test_build_mcp_auth_headers_supports_delegated_and_secret_ref() -> None:
    """delegated bearer と secret reference のヘッダーを構築できる。"""
    delegated_headers = build_mcp_auth_headers(
        McpAuthConfig(mode=McpAuthMode.DELEGATED_BEARER),
        delegated_bearer_token="delegated-token",
    )
    secret_headers = build_mcp_auth_headers(
        McpAuthConfig(
            mode=McpAuthMode.API_KEY_SECRET_REF,
            api_key_header_name="Ocp-Apim-Subscription-Key",
            api_key_secret_ref="IMPROVEMENT_MCP_API_KEY",
        ),
        secret_resolver=lambda secret_ref: "resolved-secret" if secret_ref == "IMPROVEMENT_MCP_API_KEY" else "",
    )

    assert delegated_headers == {"Authorization": "Bearer delegated-token"}
    assert secret_headers == {"Ocp-Apim-Subscription-Key": "resolved-secret"}


def test_project_connection_auth_mode_allows_foundry_managed_reference() -> None:
    """project connection mode は endpoint なしでも managed connection 参照を検証できる。"""
    entry = _entry(
        endpoint="",
        allowed_hosts=(),
        auth=McpAuthConfig(
            mode=McpAuthMode.PROJECT_CONNECTION,
            project_connection_name="m365copilot-connection",
            server_label="mcp_M365Copilot",
        ),
        access_mode=McpAccessMode.READ_ONLY,
        approval_policy=McpApprovalPolicy.DENY_WRITES,
        least_privilege=McpLeastPrivilegeMetadata(
            purpose="Read approved Work IQ context through a Foundry project connection.",
            data_classification="confidential",
            allowed_operations=("search_records", "update_record"),
            required_scopes=("User.Read",),
        ),
    )

    assert validate_mcp_registry_entry(entry) == []
    assert build_mcp_auth_headers(entry.auth) == {}


def test_build_mcp_auth_headers_fails_closed_for_missing_secret() -> None:
    """secret reference が解決できない場合は fail closed。"""
    with pytest.raises(ValueError, match="api key secret is missing"):
        build_mcp_auth_headers(
            McpAuthConfig(
                mode=McpAuthMode.API_KEY_SECRET_REF,
                api_key_header_name="x-functions-key",
                api_key_secret_ref="MISSING_SECRET",
            ),
            secret_resolver=lambda _secret_ref: "",
        )


def test_improvement_mcp_registry_uses_secret_reference_and_redacted_telemetry() -> None:
    """既存 improvement MCP 設定は secret value ではなく reference を registry に持つ。"""
    entry = build_improvement_mcp_registry_entry(
        {
            "improvement_mcp_endpoint": "https://example.azure-api.net/improvement-mcp/runtime/webhooks/mcp",
            "improvement_mcp_api_key": "real-secret-value",
            "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
        }
    )

    assert entry is not None
    assert validate_mcp_registry_entry(entry) == []
    assert entry.auth.api_key_secret_ref == "IMPROVEMENT_MCP_API_KEY"
    telemetry = mcp_registry_telemetry(entry)
    assert telemetry["credential_reference"] == "[REDACTED]"
    assert "real-secret-value" not in str(telemetry)
