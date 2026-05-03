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
    assert "Work IQ MCP + Web Search 利用ガイド" in kwargs["input"]
    assert "Web Search ツールを最低 1 回は呼び出して" in kwargs["input"]
    assert "ユーザー入力:\ntest input" in kwargs["input"]
    assert kwargs["extra_body"] == {
        "agent_reference": {"name": "travel-marketing-plan-gpt-5-4-mini", "type": "agent_reference"},
        "tool_choice": {"type": "mcp", "server_label": "mcp_M365Copilot"},
    }
    assert kwargs["extra_body"]["tool_choice"] != "required"
    assert "tool_choice" not in kwargs
    assert "tools" not in kwargs
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


def test_build_work_iq_tool_guidance_includes_web_search_requirement() -> None:
    """Bug D fix 2026-05-03: Work IQ guidance includes mandatory Web Search instruction."""
    config = {"enabled": True, "source_scope": ["emails", "teams_chats"]}
    guidance = module._build_work_iq_tool_guidance(config)

    assert "Work IQ" in guidance
    assert "Web Search" in guidance
    assert "最低 1 回は呼び出して" in guidance
    assert "禁止" in guidance


def test_build_marketing_plan_agent_definition_instructions_mention_web_search_compliance() -> None:
    """Agent definition の instructions も Web Search 利用を強調する。"""
    definition = module.build_marketing_plan_agent_definition("gpt-5-4-mini")

    instructions = definition.as_dict()["instructions"]
    # 既存 web_search tool は attach されているが、これまでは「使ってもよい」レベル
    # だったため LLM が Work IQ で済ませてしまうケースがあった (Bug D)。instructions
    # 側でも Web Search 呼び出しを必須化する文言が含まれていることを確認する。
    assert "web_search" in instructions or "Web Search" in instructions
    # _WORK_IQ_BASELINE_GUIDANCE 側にも mandatory ルールが入っていること (rubber-duck NB#2)。
    assert "最低 1 回は呼び出して" in instructions
    assert "禁止" in instructions


def test_detect_marketing_plan_tool_usage_detects_bing_grounding_call() -> None:
    """Bug D rubber-duck NB#1: bing_grounding_call も Web Search として扱う。

    chat.py:_TOOL_CALL_TYPE_MAP は bing_grounding_call を web_search にマップしている。
    Foundry preview SDK が bing_grounding_call で返すケースを Web Search として
    検出しないと、本当は Web Search が呼ばれているのに WARN ログが false-positive で
    噴き出してしまう。
    """
    response = SimpleNamespace(
        output=[
            SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot"),
            SimpleNamespace(type="bing_grounding_call"),
        ],
    )
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is True
    assert web_search_called is True


def test_detect_marketing_plan_tool_usage_traverses_nested_output() -> None:
    """Bug D rubber-duck NB#1: nested item.output / item.contents も走査する。

    Foundry preview SDK は実行サマリ item の中に nested output / contents を入れて
    返すケースがある。top-level だけ見ていると tool 呼び出しを取りこぼす。
    """
    nested = SimpleNamespace(
        type="run_summary",
        output=[
            SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot"),
        ],
        contents=[
            SimpleNamespace(type="web_search_call"),
        ],
    )
    response = SimpleNamespace(output=[nested])
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is True
    assert web_search_called is True


def test_detect_marketing_plan_tool_usage_detects_both_tools() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot"),
            SimpleNamespace(type="web_search_call"),
            SimpleNamespace(type="message"),
        ],
    )
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is True
    assert web_search_called is True


def test_detect_marketing_plan_tool_usage_handles_dict_output() -> None:
    response = SimpleNamespace(
        output=[
            {"type": "mcp_call", "server_label": "mcp_M365Copilot"},
            {"type": "web_search_call_completed"},
        ],
    )
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is True
    assert web_search_called is True


def test_detect_marketing_plan_tool_usage_when_only_work_iq() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot"),
            SimpleNamespace(type="message"),
        ],
    )
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is True
    assert web_search_called is False


def test_detect_marketing_plan_tool_usage_when_neither() -> None:
    response = SimpleNamespace(output=[SimpleNamespace(type="message")])
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is False
    assert web_search_called is False


def test_detect_marketing_plan_tool_usage_handles_missing_output() -> None:
    response = SimpleNamespace()  # no output attribute at all
    work_iq_called, web_search_called = module._detect_marketing_plan_tool_usage(response)
    assert work_iq_called is False
    assert web_search_called is False


def test_run_marketing_plan_prompt_agent_logs_warning_when_web_search_missing(
    monkeypatch, caplog
) -> None:
    """Bug D fix 2026-05-03: Work IQ enabled で Web Search 不在は WARN ログを出す。"""
    responses_client = _FakeResponsesClient()

    # Override create() to return a response that has Work IQ call but no web_search.
    def _create(**kwargs):
        responses_client.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_no_web_search",
            output=[SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot")],
        )

    responses_client.create = _create  # type: ignore[assignment]
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

    import logging

    with caplog.at_level(logging.WARNING, logger=module.logger.name):
        module.run_marketing_plan_prompt_agent(
            "test input",
            work_iq={"enabled": True, "source_scope": ["emails"]},
            work_iq_access_token="delegated-token",
        )

    assert any(
        "Web Search tool が呼ばれませんでした" in rec.getMessage() for rec in caplog.records
    ), [rec.getMessage() for rec in caplog.records]


def test_run_marketing_plan_prompt_agent_logs_info_when_both_tools_called(
    monkeypatch, caplog
) -> None:
    """Work IQ + Web Search 両方呼ばれた場合は INFO ログ (compliance OK)。"""
    responses_client = _FakeResponsesClient()

    def _create(**kwargs):
        responses_client.calls.append(kwargs)
        return SimpleNamespace(
            id="resp_with_both",
            output=[
                SimpleNamespace(type="mcp_call", server_label="mcp_M365Copilot"),
                SimpleNamespace(type="web_search_call"),
            ],
        )

    responses_client.create = _create  # type: ignore[assignment]
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

    import logging

    with caplog.at_level(logging.INFO, logger=module.logger.name):
        module.run_marketing_plan_prompt_agent(
            "test input",
            work_iq={"enabled": True, "source_scope": ["emails"]},
            work_iq_access_token="delegated-token",
        )

    assert any(
        "Work IQ + Web Search 両方を確認" in rec.getMessage() for rec in caplog.records
    ), [rec.getMessage() for rec in caplog.records]
    assert not any(
        "Web Search tool が呼ばれませんでした" in rec.getMessage() for rec in caplog.records
    )
