"""improvement_mcp モジュールのテスト。"""

import json

import httpx
import pytest

from src import improvement_mcp as improvement_mcp_module
from src.mcp_auth_registry import (
    McpAccessMode,
    McpApprovalPolicy,
    McpAuthConfig,
    McpAuthMode,
    McpLeastPrivilegeMetadata,
    McpServerRegistryEntry,
)


class _FakeMcpClient:
    """JSON-RPC 呼び出し順を検証する簡易クライアント。"""

    def __init__(self, tool_payload: dict):
        self.requests: list[dict] = []
        self._tool_payload = tool_payload

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        self.requests.append({"method": "POST", "url": url, "json": json, "headers": headers})
        request = httpx.Request("POST", url)
        rpc_method = (json or {}).get("method")

        if rpc_method == "initialize":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "Mcp-Session-Id": "session-1"},
                json={
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "test-server", "version": "1.0.0"},
                    },
                },
                request=request,
            )

        if rpc_method == "notifications/initialized":
            return httpx.Response(202, request=request)

        if rpc_method == "tools/call":
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": json["id"],
                    "result": {"structuredContent": self._tool_payload},
                },
                request=request,
            )

        raise AssertionError(f"unexpected RPC method: {rpc_method}")

    async def delete(self, url: str, headers: dict | None = None):
        self.requests.append({"method": "DELETE", "url": url, "headers": headers})
        return httpx.Response(204, request=httpx.Request("DELETE", url))


@pytest.mark.asyncio
async def test_generate_improvement_brief_performs_initialize_and_tool_call(monkeypatch) -> None:
    """MCP 呼び出しは initialize -> initialized -> tools/call を踏む"""
    fake_client = _FakeMcpClient(
        {
            "evaluation_summary": "優先課題 1 件を検出しました。",
            "improvement_brief": "訴求を具体化してください。",
            "priority_issues": [
                {
                    "label": "関連性",
                    "reason": "スコア 2.0/5",
                    "suggested_action": "ターゲット向けの便益を補強する",
                }
            ],
            "must_keep": ["タイトル: 春の沖縄旅"],
        }
    )

    monkeypatch.setattr(improvement_mcp_module, "get_http_client", lambda: fake_client)
    monkeypatch.setattr(
        improvement_mcp_module,
        "get_settings",
        lambda: {
            "improvement_mcp_endpoint": "https://example.test/mcp",
            "improvement_mcp_api_key": "secret-key",
            "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
        },
    )

    result = await improvement_mcp_module.generate_improvement_brief(
        plan_markdown="# 春の沖縄旅",
        evaluation_result={"builtin": {"relevance": {"score": 2, "reason": "具体性不足"}}},
        regulation_summary="⚠ 最安値表現に注意",
        rejection_history=["家族向けの温度感を維持してほしい"],
        user_feedback="評価を踏まえて改善してください",
    )

    assert result is not None
    assert result["improvement_brief"] == "訴求を具体化してください。"
    post_requests = [request for request in fake_client.requests if request["method"] == "POST"]
    assert [request["json"]["method"] for request in post_requests] == [
        "initialize",
        "notifications/initialized",
        "tools/call",
    ]
    assert post_requests[1]["headers"]["MCP-Protocol-Version"] == "2025-06-18"
    assert post_requests[2]["headers"]["Mcp-Session-Id"] == "session-1"
    assert post_requests[2]["headers"]["Ocp-Apim-Subscription-Key"] == "secret-key"
    assert post_requests[2]["json"]["params"]["name"] == "generate_improvement_brief"
    assert json.loads(post_requests[2]["json"]["params"]["arguments"]["rejection_history"]) == [
        "家族向けの温度感を維持してほしい"
    ]
    assert any(request["method"] == "DELETE" for request in fake_client.requests)


@pytest.mark.asyncio
async def test_generate_improvement_brief_returns_none_on_auth_header_failure(monkeypatch) -> None:
    """MCP 認証ヘッダーが組み立てられない場合は改善フローを fail closed する。"""
    registry_entry = McpServerRegistryEntry(
        server_id="improvement-mcp",
        display_name="Improvement MCP",
        endpoint="https://example.test/mcp",
        allowed_hosts=("example.test",),
        allowed_tools=("generate_improvement_brief",),
        auth=McpAuthConfig(
            mode=McpAuthMode.API_KEY_SECRET_REF,
            api_key_header_name="Ocp-Apim-Subscription-Key",
            api_key_secret_ref="MISSING_SECRET",
        ),
        access_mode=McpAccessMode.READ_ONLY,
        approval_policy=McpApprovalPolicy.DENY_WRITES,
        least_privilege=McpLeastPrivilegeMetadata(
            purpose="Generate an internal improvement brief.",
            data_classification="internal",
            allowed_operations=("generate_improvement_brief",),
            credential_reference="MISSING_SECRET",
        ),
    )
    fake_client = _FakeMcpClient({})

    monkeypatch.setattr(improvement_mcp_module, "get_http_client", lambda: fake_client)
    monkeypatch.setattr(improvement_mcp_module, "build_improvement_mcp_registry_entry", lambda _settings: registry_entry)
    monkeypatch.setattr(
        improvement_mcp_module,
        "get_settings",
        lambda: {
            "improvement_mcp_endpoint": "https://example.test/mcp",
            "improvement_mcp_api_key": "",
            "improvement_mcp_api_key_header": "Ocp-Apim-Subscription-Key",
        },
    )

    result = await improvement_mcp_module.generate_improvement_brief(
        plan_markdown="# 春の沖縄旅",
        evaluation_result={},
        regulation_summary="",
        rejection_history=[],
        user_feedback="改善してください",
    )

    assert result is None
    assert fake_client.requests == []


def test_parse_tool_result_accepts_text_content() -> None:
    """tool result の text content からも改善ブリーフを復元できる"""
    payload = {
        "evaluation_summary": "summary",
        "improvement_brief": "brief",
        "priority_issues": [{"label": "関連性", "reason": "reason", "suggested_action": "action"}],
        "must_keep": ["タイトル: 春の沖縄旅"],
    }

    result = improvement_mcp_module._parse_tool_result(
        {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    )

    assert result["improvement_brief"] == "brief"
    assert result["priority_issues"][0]["label"] == "関連性"


def test_parse_tool_result_accepts_python_literal_text_content() -> None:
    """Azure Functions MCP extension の Python リテラル表現も受け入れる"""
    payload = {
        "evaluation_summary": "summary",
        "improvement_brief": "brief",
        "priority_issues": [{"label": "関連性", "reason": "reason", "suggested_action": "action"}],
        "must_keep": ["タイトル: 春の沖縄旅"],
    }

    result = improvement_mcp_module._parse_tool_result({"content": [{"type": "text", "text": str(payload)}]})

    assert result["improvement_brief"] == "brief"
    assert result["must_keep"] == ["タイトル: 春の沖縄旅"]


def test_next_request_id_uses_numeric_string() -> None:
    """Azure Functions MCP 互換のため request id は数値文字列にする"""
    request_id = improvement_mcp_module._next_request_id()

    assert request_id.isdigit()
