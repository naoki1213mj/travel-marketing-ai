"""品質レビューエージェント生成のテスト。"""

import sys
import types

from src.agents import quality_review as review_module


def test_create_review_agent_uses_foundry_fallback_when_github_review_is_disabled(monkeypatch) -> None:
    """GitHub review を opt-in にし、既定は Foundry fallback を使う。"""
    captured: dict[str, object] = {}
    sentinel_agent = object()

    class _FakeFoundryChatClient:
        def __init__(self, *, project_endpoint: str, credential: object, model: str) -> None:
            captured["project_endpoint"] = project_endpoint
            captured["credential"] = credential
            captured["model"] = model

        def as_agent(self, **kwargs):
            captured["as_agent_kwargs"] = kwargs
            return sentinel_agent

    fake_foundry = types.ModuleType("agent_framework.foundry")
    fake_foundry.FoundryChatClient = _FakeFoundryChatClient
    fake_identity = types.ModuleType("azure.identity")
    fake_identity.DefaultAzureCredential = lambda: "credential"

    monkeypatch.setitem(sys.modules, "agent_framework.foundry", fake_foundry)
    monkeypatch.setitem(sys.modules, "azure.identity", fake_identity)
    monkeypatch.setattr(
        review_module,
        "get_settings",
        lambda: {
            "project_endpoint": "https://example.test",
            "model_name": "gpt-5-4-mini",
            "enable_github_copilot_review_agent": "false",
        },
    )

    result = review_module.create_review_agent()

    assert result is sentinel_agent
    assert captured["project_endpoint"] == "https://example.test"
    assert captured["credential"] == "credential"
    assert captured["model"] == "gpt-5-4-mini"
    assert captured["as_agent_kwargs"]["name"] == "quality-review-agent"


def test_create_review_agent_uses_github_copilot_when_enabled(monkeypatch) -> None:
    """GitHub review opt-in 時は PermissionHandler なしで GitHubCopilotAgent を使う。"""
    captured: dict[str, object] = {}

    class _FakeGitHubCopilotAgent:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    fake_github = types.ModuleType("agent_framework.github")
    fake_github.GitHubCopilotAgent = _FakeGitHubCopilotAgent

    monkeypatch.setitem(sys.modules, "agent_framework.github", fake_github)
    monkeypatch.setattr(
        review_module,
        "get_settings",
        lambda: {
            "project_endpoint": "",
            "model_name": "gpt-5-4-mini",
            "enable_github_copilot_review_agent": "true",
        },
    )

    result = review_module.create_review_agent()

    assert isinstance(result, _FakeGitHubCopilotAgent)
    assert captured["name"] == "quality-review-agent"
    assert captured["instructions"] == review_module.INSTRUCTIONS
    assert captured["tools"] == review_module._REVIEW_TOOLS
    assert "on_permission_request" not in captured
