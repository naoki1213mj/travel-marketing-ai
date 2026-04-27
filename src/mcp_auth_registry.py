"""MCP サーバー認証レジストリと安全ポリシー判定。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from urllib.parse import urlparse


class McpAuthMode(StrEnum):
    """MCP サーバーの認証方式。"""

    PROJECT_CONNECTION = "project_connection"
    DELEGATED_BEARER = "delegated_bearer"
    API_KEY_SECRET_REF = "api_key_secret_ref"
    NONE = "none"


class McpAccessMode(StrEnum):
    """MCP サーバーのアクセス範囲。"""

    READ_ONLY = "read_only"
    READ_WRITE = "read_write"


class McpApprovalPolicy(StrEnum):
    """書き込み系ツールの承認ポリシー。"""

    REQUIRE_FOR_WRITES = "require_for_writes"
    DENY_WRITES = "deny_writes"
    ALLOW_CONFIGURED_WRITES = "allow_configured_writes"


@dataclass(frozen=True)
class McpAuthConfig:
    """認証方式ごとの最小構成。"""

    mode: McpAuthMode
    project_connection_name: str = ""
    server_label: str = ""
    delegated_audience: str = ""
    api_key_header_name: str = ""
    api_key_secret_ref: str = ""


@dataclass(frozen=True)
class McpLeastPrivilegeMetadata:
    """最小権限レビューのためのメタデータ。"""

    purpose: str
    data_classification: str
    allowed_operations: tuple[str, ...] = field(default_factory=tuple)
    required_scopes: tuple[str, ...] = field(default_factory=tuple)
    credential_reference: str = ""
    owner: str = ""


@dataclass(frozen=True)
class McpServerRegistryEntry:
    """許可済み MCP サーバーのレジストリ定義。"""

    server_id: str
    display_name: str
    endpoint: str
    allowed_hosts: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    auth: McpAuthConfig
    access_mode: McpAccessMode = McpAccessMode.READ_ONLY
    approval_policy: McpApprovalPolicy = McpApprovalPolicy.DENY_WRITES
    write_tools: tuple[str, ...] = field(default_factory=tuple)
    least_privilege: McpLeastPrivilegeMetadata | None = None


@dataclass(frozen=True)
class McpToolPolicyDecision:
    """MCP ツール呼び出し可否の判定結果。"""

    allowed: bool
    approval_required: bool
    reason: str
    telemetry: dict[str, object]


SecretResolver = Callable[[str], str]

_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def build_improvement_mcp_registry_entry(settings: Mapping[str, str]) -> McpServerRegistryEntry | None:
    """既存 improvement MCP 設定を安全なレジストリ定義へ変換する。"""
    endpoint = settings.get("improvement_mcp_endpoint", "").strip()
    if not endpoint:
        return None

    host = _extract_host(endpoint)
    api_key = settings.get("improvement_mcp_api_key", "").strip()
    header_name = settings.get("improvement_mcp_api_key_header", "Ocp-Apim-Subscription-Key").strip()
    auth = (
        McpAuthConfig(
            mode=McpAuthMode.API_KEY_SECRET_REF,
            api_key_header_name=header_name or "Ocp-Apim-Subscription-Key",
            api_key_secret_ref="IMPROVEMENT_MCP_API_KEY",
        )
        if api_key
        else McpAuthConfig(mode=McpAuthMode.NONE)
    )
    credential_reference = "IMPROVEMENT_MCP_API_KEY" if api_key else ""
    return McpServerRegistryEntry(
        server_id="improvement-mcp",
        display_name="Improvement MCP",
        endpoint=endpoint,
        allowed_hosts=(host,) if host else (),
        allowed_tools=("generate_improvement_brief",),
        auth=auth,
        access_mode=McpAccessMode.READ_ONLY,
        approval_policy=McpApprovalPolicy.DENY_WRITES,
        write_tools=(),
        least_privilege=McpLeastPrivilegeMetadata(
            purpose="Generate an internal improvement brief for a rejected marketing plan.",
            data_classification="internal",
            allowed_operations=("generate_improvement_brief",),
            credential_reference=credential_reference,
            owner="travel-marketing-backend",
        ),
    )


def validate_mcp_registry_entry(entry: McpServerRegistryEntry) -> list[str]:
    """MCP レジストリ定義の安全性を検証する。"""
    errors: list[str] = []
    if not entry.server_id.strip():
        errors.append("server_id_required")
    if not entry.allowed_tools:
        errors.append("allowed_tools_required")

    host = _extract_host(entry.endpoint)
    if entry.endpoint:
        if not host:
            errors.append("endpoint_host_required")
        if host and not _is_https_or_local(entry.endpoint, host):
            errors.append("https_required")
        if host and not _host_allowed(host, entry.allowed_hosts):
            errors.append("host_not_allowed")
    elif entry.auth.mode != McpAuthMode.PROJECT_CONNECTION:
        errors.append("endpoint_required")

    allowed_tools = set(entry.allowed_tools)
    for tool_name in entry.write_tools:
        if tool_name not in allowed_tools:
            errors.append(f"write_tool_not_allowed:{tool_name}")

    errors.extend(_validate_auth(entry))
    errors.extend(_validate_least_privilege(entry))
    return errors


def decide_mcp_tool_policy(
    entry: McpServerRegistryEntry,
    tool_name: str,
    *,
    operation: str = "read",
    approval_granted: bool = False,
) -> McpToolPolicyDecision:
    """許可ツール・書き込み承認・read-only 制約をまとめて判定する。"""
    normalized_tool = tool_name.strip()
    validation_errors = validate_mcp_registry_entry(entry)
    telemetry = _build_policy_telemetry(entry, normalized_tool, operation)
    if validation_errors:
        telemetry["validation_errors"] = validation_errors
        return McpToolPolicyDecision(False, False, "registry_invalid", telemetry)

    if normalized_tool not in entry.allowed_tools:
        return McpToolPolicyDecision(False, False, "tool_not_allowed", telemetry)

    is_write = operation.strip().lower() in {"write", "mutation", "mutating"} or normalized_tool in entry.write_tools
    telemetry["write_operation"] = is_write
    if is_write and entry.access_mode == McpAccessMode.READ_ONLY:
        return McpToolPolicyDecision(False, False, "read_only_mode", telemetry)
    if not is_write:
        return McpToolPolicyDecision(True, False, "allowed_read", telemetry)

    if entry.approval_policy == McpApprovalPolicy.DENY_WRITES:
        return McpToolPolicyDecision(False, False, "write_denied", telemetry)
    if entry.approval_policy == McpApprovalPolicy.REQUIRE_FOR_WRITES and not approval_granted:
        return McpToolPolicyDecision(False, True, "approval_required", telemetry)
    return McpToolPolicyDecision(True, False, "allowed_write", telemetry)


def build_mcp_auth_headers(
    auth: McpAuthConfig,
    *,
    secret_resolver: SecretResolver | None = None,
    delegated_bearer_token: str = "",
) -> dict[str, str]:
    """認証定義から HTTP ヘッダーを作る。シークレット値は registry に保持しない。"""
    if auth.mode in {McpAuthMode.NONE, McpAuthMode.PROJECT_CONNECTION}:
        return {}
    if auth.mode == McpAuthMode.DELEGATED_BEARER:
        token = delegated_bearer_token.strip()
        if not token:
            raise ValueError("delegated bearer token is required")
        return {"Authorization": f"Bearer {token}"}
    if auth.mode == McpAuthMode.API_KEY_SECRET_REF:
        if secret_resolver is None:
            raise ValueError("secret resolver is required")
        header_name = auth.api_key_header_name.strip()
        if not _HEADER_NAME_PATTERN.fullmatch(header_name):
            raise ValueError("invalid api key header name")
        secret = secret_resolver(auth.api_key_secret_ref).strip()
        if not secret:
            raise ValueError("api key secret is missing")
        return {header_name: secret}
    raise ValueError(f"unsupported MCP auth mode: {auth.mode}")


def mcp_registry_telemetry(entry: McpServerRegistryEntry) -> dict[str, object]:
    """シークレットを含まない MCP registry telemetry を返す。"""
    metadata = entry.least_privilege
    return {
        "mcp_server_id": entry.server_id,
        "display_name": entry.display_name,
        "endpoint_host": _extract_host(entry.endpoint),
        "auth_mode": entry.auth.mode.value,
        "access_mode": entry.access_mode.value,
        "approval_policy": entry.approval_policy.value,
        "allowed_tools": list(entry.allowed_tools),
        "write_tools": list(entry.write_tools),
        "least_privilege_scopes": list(metadata.required_scopes) if metadata else [],
        "least_privilege_operations": list(metadata.allowed_operations) if metadata else [],
        "credential_reference": "[REDACTED]" if entry.auth.api_key_secret_ref else "",
    }


def _validate_auth(entry: McpServerRegistryEntry) -> list[str]:
    errors: list[str] = []
    auth = entry.auth
    if auth.mode == McpAuthMode.NONE:
        if entry.access_mode != McpAccessMode.READ_ONLY:
            errors.append("none_auth_requires_read_only")
    elif auth.mode == McpAuthMode.PROJECT_CONNECTION:
        if not auth.project_connection_name.strip() and not auth.server_label.strip():
            errors.append("project_connection_reference_required")
    elif auth.mode == McpAuthMode.DELEGATED_BEARER:
        if not auth.delegated_audience.strip():
            errors.append("delegated_audience_required")
    elif auth.mode == McpAuthMode.API_KEY_SECRET_REF:
        if not auth.api_key_secret_ref.strip():
            errors.append("api_key_secret_ref_required")
        if not auth.api_key_header_name.strip():
            errors.append("api_key_header_required")
        elif not _HEADER_NAME_PATTERN.fullmatch(auth.api_key_header_name.strip()):
            errors.append("api_key_header_invalid")
    return errors


def _validate_least_privilege(entry: McpServerRegistryEntry) -> list[str]:
    errors: list[str] = []
    metadata = entry.least_privilege
    if metadata is None:
        return ["least_privilege_metadata_required"]
    if not metadata.purpose.strip():
        errors.append("least_privilege_purpose_required")
    if not metadata.data_classification.strip():
        errors.append("least_privilege_data_classification_required")
    if not metadata.allowed_operations:
        errors.append("least_privilege_allowed_operations_required")
    for operation in metadata.allowed_operations:
        if operation not in entry.allowed_tools:
            errors.append(f"least_privilege_operation_not_allowed:{operation}")
    if entry.auth.mode == McpAuthMode.DELEGATED_BEARER and not metadata.required_scopes:
        errors.append("delegated_scopes_required")
    if entry.auth.mode == McpAuthMode.API_KEY_SECRET_REF and metadata.credential_reference != entry.auth.api_key_secret_ref:
        errors.append("credential_reference_mismatch")
    return errors


def _build_policy_telemetry(entry: McpServerRegistryEntry, tool_name: str, operation: str) -> dict[str, object]:
    telemetry = mcp_registry_telemetry(entry)
    telemetry["tool"] = tool_name
    telemetry["operation"] = operation.strip().lower() or "read"
    return telemetry


def _extract_host(endpoint: str) -> str:
    parsed = urlparse(endpoint.strip())
    return (parsed.hostname or "").lower()


def _is_https_or_local(endpoint: str, host: str) -> bool:
    scheme = urlparse(endpoint.strip()).scheme.lower()
    return scheme == "https" or host in _LOCAL_HOSTS


def _host_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    normalized = host.lower()
    for allowed_host in allowed_hosts:
        allowed = allowed_host.strip().lower()
        if not allowed:
            continue
        if allowed == normalized:
            return True
        if allowed.startswith("*.") and normalized.endswith(allowed[1:]):
            return True
    return False
