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


class _FakeProjectClient:
    def __init__(self, responses_client: _FakeResponsesClient, agent_name: str = "travel-marketing-plan-gpt-5-4-mini") -> None:
        self._responses_client = responses_client
        self.agents = SimpleNamespace(get=lambda agent_name=agent_name: SimpleNamespace(name=agent_name))

    def get_openai_client(self) -> _FakeResponsesClient:
        return self._responses_client

    def close(self) -> None:
        return None


def test_run_marketing_plan_prompt_agent_uses_agent_reference_without_work_iq_tools(monkeypatch) -> None:
    """Work IQ connector を使わない場合は agent_reference 経路を維持する。"""
    responses_client = _FakeResponsesClient()
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test",
            "model_name": "gpt-5-4-mini",
            "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        },
    )
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _FakeProjectClient(responses_client))

    result = module.run_marketing_plan_prompt_agent("test input")

    assert result == {"id": "resp_123"}
    assert len(responses_client.calls) == 1
    kwargs = responses_client.calls[0]
    assert kwargs["input"] == "test input"
    assert kwargs["extra_body"] == {"agent_reference": {"name": "travel-marketing-plan-gpt-5-4-mini", "type": "agent_reference"}}
    assert "tools" not in kwargs
    assert "model" not in kwargs
    assert "instructions" not in kwargs


def test_build_marketing_plan_agent_definition_includes_work_iq_guidance() -> None:
    """事前作成 Agent 定義には Work IQ tool 利用方針を含める。"""
    definition = module.build_marketing_plan_agent_definition("gpt-5-4-mini")

    instructions = definition.as_dict()["instructions"]
    assert "Work IQ / Microsoft 365 tools の利用方針" in instructions
    assert "優先利用してください" in instructions


def test_run_marketing_plan_prompt_agent_overlays_work_iq_tools_on_agent_reference(monkeypatch) -> None:
    """Work IQ connector 利用時も agent_reference を維持しつつ overlay する。"""
    responses_client = _FakeResponsesClient()
    emitted_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test",
            "model_name": "gpt-5-4-mini",
            "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        },
    )
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _FakeProjectClient(responses_client))
    monkeypatch.setattr(module, "emit_tool_event", lambda payload: emitted_events.append(payload) or payload)

    result = module.run_marketing_plan_prompt_agent(
        "test input",
        work_iq={"enabled": True, "source_scope": ["emails", "teams_chats"]},
        work_iq_access_token="delegated-token",
    )

    assert result == {"id": "resp_123"}
    assert len(responses_client.calls) == 1
    kwargs = responses_client.calls[0]
    assert kwargs["extra_body"] == {"agent_reference": {"name": "travel-marketing-plan-gpt-5-4-mini", "type": "agent_reference"}}
    assert "Microsoft 365 参照ガイド" in kwargs["input"]
    assert "ユーザー入力:\ntest input" in kwargs["input"]
    assert "Work IQ Mail" in kwargs["input"]
    assert "Work IQ Teams" in kwargs["input"]
    tools = kwargs["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 2
    assert tools[0].as_dict()["connector_id"] == "connector_outlookemail"
    assert tools[1].as_dict()["connector_id"] == "connector_microsoftteams"
    assert tools[0].as_dict()["require_approval"] == "never"
    assert tools[1].as_dict()["require_approval"] == "never"
    assert [event["status"] for event in emitted_events] == ["running", "completed"]
    assert all(event["tool"] == "workiq_foundry_tool" for event in emitted_events)


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

    monkeypatch.setattr(
        module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test",
            "model_name": "gpt-5-4-mini",
            "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        },
    )
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "ResourceNotFoundError", FakeNotFoundError)
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: _MissingProjectClient())

    try:
        module.run_marketing_plan_prompt_agent("test input")
    except ValueError as exc:
        assert "scripts/postprovision.py" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")


def test_build_work_iq_tools_prefers_teams_for_meeting_notes() -> None:
    """meeting_notes は不要な calendar connector を混ぜない。"""
    tools, resolved_tools = module._build_work_iq_tools(
        {"enabled": True, "source_scope": ["meeting_notes", "teams_chats"]},
        "delegated-token",
    )

    assert [tool.as_dict()["connector_id"] for tool in tools] == ["connector_microsoftteams"]
    assert [tool["display_name"] for tool in resolved_tools] == ["Work IQ Teams"]


def test_sync_marketing_plan_agent_creates_new_version(monkeypatch) -> None:
    """postprovision 用 helper は create_version で agent を同期する。"""

    class _FakeAgents:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create_version(self, *, agent_name: str, definition):
            self.calls.append({"agent_name": agent_name, "definition": definition})
            return SimpleNamespace(name=agent_name)

    class _SyncProjectClient:
        def __init__(self) -> None:
            self.agents = _FakeAgents()
            self.closed = False

        def close(self) -> None:
            self.closed = True

    fake_client = _SyncProjectClient()
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
    assert fake_client.agents.calls[0]["agent_name"] == "travel-marketing-plan-gpt-5-4-mini"
    assert fake_client.agents.calls[0]["definition"].as_dict()["tools"][0]["type"] == "web_search"
