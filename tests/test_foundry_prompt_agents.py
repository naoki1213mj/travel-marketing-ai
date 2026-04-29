"""Foundry Prompt Agent 実行ラッパーのテスト。"""

from types import SimpleNamespace

from src import foundry_prompt_agents as module


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "resp_123"}

    def close(self) -> None:
        return None


class _FakeConnection:
    def __init__(self, name: str, conn_type: str, target: str) -> None:
        self.name = name
        self.type = conn_type
        self.target = target


class _FakeConnections:
    def __init__(self, items: list[_FakeConnection]) -> None:
        self._items = items

    def list(self):
        return list(self._items)


class _FakeAgentDetails:
    def __init__(self, agent_name: str, tools: list[dict] | None = None) -> None:
        self.name = agent_name
        self._tools = tools or []

    def as_dict(self) -> dict:
        return {
            "versions": {
                "latest": {
                    "definition": {
                        "tools": self._tools,
                    }
                }
            }
        }


class _FakeAgents:
    def __init__(self, agent_name: str, tools: list[dict] | None = None) -> None:
        self._details = _FakeAgentDetails(agent_name, tools)
        self.calls: list[dict[str, object]] = []

    def get(self, *, agent_name: str):
        return _FakeAgentDetails(agent_name, self._details._tools)

    def create_version(self, *, agent_name: str, definition):
        self.calls.append({"agent_name": agent_name, "definition": definition})
        return SimpleNamespace(name=agent_name)


class _FakeProjectClient:
    def __init__(
        self,
        responses_client: _FakeResponsesClient,
        *,
        agent_name: str = "travel-marketing-plan-gpt-5-4-mini",
        agent_tools: list[dict] | None = None,
        connections: list[_FakeConnection] | None = None,
    ) -> None:
        self._responses_client = responses_client
        self.agents = _FakeAgents(agent_name, agent_tools)
        self.connections = _FakeConnections(connections or [])
        self.closed = False
        self.openai_client_kwargs: list[dict[str, object]] = []

    def get_openai_client(self, **kwargs) -> _FakeResponsesClient:
        self.openai_client_kwargs.append(kwargs)
        return self._responses_client

    def close(self) -> None:
        self.closed = True


def _settings() -> dict[str, str]:
    return {
        "project_endpoint": "https://example.test",
        "model_name": "gpt-5-4-mini",
        "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        "enable_gpt_55": "false",
        "gpt_55_deployment_name": "",
        "enable_model_router": "false",
        "model_router_endpoint": "",
        "model_router_deployment_name": "",
        "model_deployment_allowlist": "",
    }


def test_run_marketing_plan_prompt_agent_uses_agent_reference_without_work_iq_tools(monkeypatch) -> None:
    """Work IQ を使わない場合は agent_reference 経路を維持する。"""
    responses_client = _FakeResponsesClient()
    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _FakeProjectClient(responses_client))

    result = module.run_marketing_plan_prompt_agent("test input")

    assert result == {"id": "resp_123"}
    kwargs = responses_client.calls[0]
    assert kwargs["model"] == "gpt-5-4-mini"
    assert kwargs["input"] == "test input"
    assert kwargs["extra_body"] == {"agent_reference": {"name": "travel-marketing-plan-gpt-5-4-mini", "type": "agent_reference"}}
    assert "tools" not in kwargs


def test_build_marketing_plan_agent_definition_includes_work_iq_guidance() -> None:
    """事前作成 Agent 定義には Work IQ tool 利用方針を含める。"""
    definition = module.build_marketing_plan_agent_definition("gpt-5-4-mini")

    instructions = definition.as_dict()["instructions"]
    assert "Work IQ / Microsoft 365 tools の利用方針" in instructions
    assert "優先利用してください" in instructions
    assert "少なくとも一度は Work IQ を参照" in instructions
    assert "推測で続行せず失敗として扱ってください" in instructions


def test_build_work_iq_mcp_tool_from_remote_tool_connection() -> None:
    """WorkIQCopilot の RemoteTool connection から MCPTool を組み立てる。"""
    fake_client = _FakeProjectClient(
        _FakeResponsesClient(),
        connections=[
            _FakeConnection(
                "WorkIQCopilot",
                "RemoteTool",
                "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
            )
        ],
    )

    tool = module._build_work_iq_mcp_tool(fake_client)

    assert tool is not None
    assert tool.as_dict()["type"] == "mcp"
    assert tool.as_dict()["server_label"] == "mcp_M365Copilot"
    assert tool.as_dict()["server_url"] == "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot"
    assert tool.as_dict()["project_connection_id"] == "WorkIQCopilot"


def test_build_marketing_plan_agent_definition_includes_work_iq_tool_when_provided() -> None:
    """Work IQ MCP tool を渡した場合は Prompt Agent 定義に含める。"""
    tool = module.MCPTool(
        server_label="mcp_M365Copilot",
        server_url="https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
        project_connection_id="WorkIQCopilot",
        require_approval="never",
    )

    definition = module.build_marketing_plan_agent_definition("gpt-5-4-mini", work_iq_tool=tool)

    tools = definition.as_dict()["tools"]
    assert [item["type"] for item in tools] == ["web_search", "mcp"]
    assert tools[1]["server_label"] == "mcp_M365Copilot"


def test_run_marketing_plan_prompt_agent_uses_agent_reference_with_work_iq_tool_choice(monkeypatch) -> None:
    """Work IQ 有効時も docs-backed な agent_reference 経路を使う。"""
    responses_client = _FakeResponsesClient()
    fake_client = _FakeProjectClient(
        responses_client,
        connections=[
            _FakeConnection(
                "WorkIQCopilot",
                "RemoteTool",
                "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
            )
        ],
    )
    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: fake_client)

    result = module.run_marketing_plan_prompt_agent(
        "test input",
        work_iq={"enabled": True, "source_scope": ["emails", "teams_chats"]},
        work_iq_access_token="delegated-token",
    )

    assert result == {"id": "resp_123"}
    kwargs = responses_client.calls[0]
    assert kwargs["model"] == "gpt-5-4-mini"
    assert "Work IQ MCP 利用ガイド" in kwargs["input"]
    assert "ユーザー入力:\ntest input" in kwargs["input"]
    assert kwargs["extra_body"] == {
        "agent_reference": {"name": "travel-marketing-plan-gpt-5-4-mini", "type": "agent_reference"},
    }
    assert kwargs["tool_choice"] == {"type": "mcp", "server_label": "mcp_M365Copilot"}
    assert kwargs["tools"] == [
        {
            "type": "mcp",
            "server_label": "mcp_M365Copilot",
            "server_url": "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
            "project_connection_id": "WorkIQCopilot",
            "require_approval": "never",
            "server_description": "Microsoft 365 workplace context tools for organizational emails, meetings, chats, and documents.",
        }
    ]
    assert fake_client.openai_client_kwargs == [{"api_key": "delegated-token"}]
    assert "instructions" not in kwargs


def test_build_work_iq_responses_tool_uses_connection_without_inline_token() -> None:
    """Work IQ MCP は connection の OAuth passthrough を使い、token を tool に直書きしない。"""
    tool = module._build_work_iq_responses_tool(
        "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
        connection_name="WorkIQCopilot",
    )

    assert tool["project_connection_id"] == "WorkIQCopilot"
    assert "authorization" not in tool


def test_run_marketing_plan_prompt_agent_raises_when_work_iq_token_missing(monkeypatch) -> None:
    """Work IQ 有効なのに delegated token が無ければ fail-closed にする。"""
    responses_client = _FakeResponsesClient()
    fake_client = _FakeProjectClient(
        responses_client,
        agent_tools=[
            {
                "type": "mcp",
                "server_label": "mcp_M365Copilot",
                "project_connection_id": "WorkIQCopilot",
            }
        ],
    )
    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: fake_client)

    try:
        module.run_marketing_plan_prompt_agent(
            "test input",
            work_iq={"enabled": True, "source_scope": ["emails"]},
            work_iq_access_token="",
        )
    except ValueError as exc:
        assert "no delegated access token" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")

    assert responses_client.calls == []


def test_run_marketing_plan_prompt_agent_raises_when_work_iq_connection_missing(monkeypatch) -> None:
    """Work IQ 有効なのに RemoteTool connection が無ければ fail-closed にする。"""
    responses_client = _FakeResponsesClient()
    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _FakeProjectClient(responses_client))

    try:
        module.run_marketing_plan_prompt_agent(
            "test input",
            work_iq={"enabled": True, "source_scope": ["emails"]},
            work_iq_access_token="delegated-token",
        )
    except ValueError as exc:
        assert "RemoteTool connection" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")

    assert responses_client.calls == []


def test_run_marketing_plan_prompt_agent_raises_when_agent_missing(monkeypatch) -> None:
    """事前作成済み Agent が無ければ明示的エラーにする。"""

    class FakeNotFoundError(Exception):
        """ResourceNotFoundError の代替。"""

    class _MissingAgents:
        def get(self, *, agent_name: str):
            raise FakeNotFoundError("missing")

    class _MissingProjectClient:
        def __init__(self) -> None:
            self.agents = _MissingAgents()

        def get_openai_client(self) -> _FakeResponsesClient:
            return _FakeResponsesClient()

        def close(self) -> None:
            return None

    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "ResourceNotFoundError", FakeNotFoundError)
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _MissingProjectClient())

    try:
        module.run_marketing_plan_prompt_agent("test input")
    except ValueError as exc:
        assert "scripts/postprovision.py" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_sync_marketing_plan_agent_creates_new_version_with_work_iq_tool(monkeypatch) -> None:
    """postprovision 用 helper は Web Search と Work IQ MCP を含む version を作る。"""
    fake_client = _FakeProjectClient(
        _FakeResponsesClient(),
        connections=[
            _FakeConnection(
                "WorkIQCopilot",
                "RemoteTool",
                "https://agent365.svc.cloud.microsoft/agents/servers/mcp_M365Copilot",
            )
        ],
    )
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: fake_client)
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: {
            "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        },
    )

    result = module.sync_marketing_plan_agent("https://example.test", "gpt-5-4-mini")

    assert result is True
    assert fake_client.closed is True
    definition_tools = fake_client.agents.calls[0]["definition"].as_dict()["tools"]
    assert [tool["type"] for tool in definition_tools] == ["web_search", "mcp"]
