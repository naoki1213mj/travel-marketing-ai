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


def test_run_marketing_plan_prompt_agent_uses_direct_tools_when_work_iq_enabled(monkeypatch) -> None:
    """Work IQ connector 利用時は agent_reference を外して tools を直接渡す。"""
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

    result = module.run_marketing_plan_prompt_agent(
        "test input",
        work_iq={"enabled": True, "source_scope": ["emails", "teams_chats"]},
        work_iq_access_token="delegated-token",
    )

    assert result == {"id": "resp_123"}
    assert len(responses_client.calls) == 1
    kwargs = responses_client.calls[0]
    assert kwargs["input"] == "test input"
    assert kwargs["model"] == "gpt-5-4-mini"
    assert kwargs["instructions"] == module.MARKETING_PLAN_INSTRUCTIONS
    assert "extra_body" not in kwargs
    tools = kwargs["tools"]
    assert isinstance(tools, list)
    assert len(tools) == 3
    assert tools[0].as_dict()["type"] == "web_search"
    assert tools[1].as_dict()["connector_id"] == "connector_outlookemail"
    assert tools[2].as_dict()["connector_id"] == "connector_microsoftteams"
    assert tools[1].as_dict()["require_approval"] == "never"
    assert tools[2].as_dict()["require_approval"] == "never"
