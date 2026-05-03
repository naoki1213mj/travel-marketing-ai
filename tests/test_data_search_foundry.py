"""data-search Foundry Prompt Agent (PR 3) のテスト。

カバー範囲:
- 2-pass logic (Pass 1 success / Pass 1 zero-fabric → Pass 2 / Pass 1 401 → Pass 2 / Pass 1 5xx → fail loud)
- delegated token 不在 → ValueError
- AZURE_AI_PROJECT_ENDPOINT 不在 → ValueError
- recoverable error 判定
- Fabric tool detection
- agent definition (`MicrosoftFabricPreviewTool` 有無、Code Interpreter 有無)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from src import foundry_prompt_agents as module


class _FakeResponses:
    def __init__(self, response_queue: list[Any]) -> None:
        self._queue = list(response_queue)
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._queue:
            return SimpleNamespace(id="resp_default", output=[])
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        return None


class _FakeOpenAIClient:
    def __init__(self, response_queue: list[Any]) -> None:
        self.responses = _FakeResponses(response_queue)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeAgents:
    def __init__(self, agent_name: str) -> None:
        self._agent_name = agent_name

    def get(self, *, agent_name: str):
        return SimpleNamespace(name=agent_name)


class _FakeProjectClient:
    def __init__(self, openai_client: _FakeOpenAIClient, agent_name: str) -> None:
        self._openai_client = openai_client
        self.agents = _FakeAgents(agent_name)
        self.openai_client_kwargs: list[dict[str, object]] = []
        self.closed = False

    def get_openai_client(self, **kwargs) -> _FakeOpenAIClient:
        self.openai_client_kwargs.append(kwargs)
        return self._openai_client

    def close(self) -> None:
        self.closed = True


def _settings() -> dict[str, str]:
    return {
        "project_endpoint": "https://example.test",
        "model_name": "gpt-5-4-mini",
        "data_search_prompt_agent_name": "travel-data-search",
        "marketing_plan_prompt_agent_name": "travel-marketing-plan",
        "foundry_fabric_connection_id": "",
        "enable_code_interpreter": "false",
        "enable_gpt_55": "false",
        "gpt_55_deployment_name": "",
        "enable_model_router": "false",
        "model_router_endpoint": "",
        "model_router_deployment_name": "",
        "model_deployment_allowlist": "",
    }


def _build_response_with_fabric() -> SimpleNamespace:
    """Pass 1 で Fabric tool が呼ばれた response を模擬する。"""
    return SimpleNamespace(
        id="resp_pass1_ok",
        output=[
            SimpleNamespace(type="fabric_dataagent_preview"),
            SimpleNamespace(type="message"),
        ],
    )


def _build_response_without_fabric() -> SimpleNamespace:
    """Pass 1 で Fabric tool が呼ばれなかった response を模擬する。"""
    return SimpleNamespace(
        id="resp_pass1_no_fabric",
        output=[SimpleNamespace(type="message")],
    )


def _patch_common(monkeypatch) -> None:
    monkeypatch.setattr(module, "get_settings", _settings)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())
    monkeypatch.setattr(module, "resolve_model_deployment", lambda name, **_: name)


def test_run_data_search_prompt_agent_pass1_success(monkeypatch) -> None:
    """Pass 1 で Fabric tool が呼ばれたら採用 (Pass 2 を発行しない)。"""
    _patch_common(monkeypatch)
    pass1_response = _build_response_with_fabric()
    openai_client = _FakeOpenAIClient([pass1_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    assert result is pass1_response
    assert len(openai_client.responses.calls) == 1, "Pass 2 should not be invoked"
    assert openai_client.responses.calls[0]["extra_body"]["agent_reference"]["name"].startswith("travel-data-search-")


def test_run_data_search_prompt_agent_pass1_zero_fabric_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で Fabric tool が呼ばれなかった場合 Pass 2 に降格する。"""
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2", output=[SimpleNamespace(type="message")])
    openai_client = _FakeOpenAIClient([_build_response_without_fabric(), pass2_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)
    monkeypatch.setattr(module, "_run_function_call_loop", _make_fake_function_call_loop(pass2_response))

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    assert result is pass2_response
    assert len(openai_client.responses.calls) == 2, "Pass 2 must be invoked after zero-fabric Pass 1"
    pass2_call = openai_client.responses.calls[1]
    assert pass2_call["tool_choice"] == "required"
    assert pass2_call["tools"], "function tools must be passed for Pass 2"


def test_run_data_search_prompt_agent_pass1_401_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で 401 が出たら Pass 2 に降格する (recoverable failure)。"""
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2_after_401", output=[])
    openai_client = _FakeOpenAIClient(
        [RuntimeError("Pass 1 failed: 401 Unauthorized OBO failure"), pass2_response]
    )
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)
    monkeypatch.setattr(module, "_run_function_call_loop", _make_fake_function_call_loop(pass2_response))

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    assert result is pass2_response
    assert len(openai_client.responses.calls) == 2


def test_run_data_search_prompt_agent_pass1_5xx_fails_loud(monkeypatch) -> None:
    """5xx / 一般 exception は Pass 2 にせず例外を伝播する。"""
    _patch_common(monkeypatch)
    openai_client = _FakeOpenAIClient([RuntimeError("Internal Server Error 500 unexpected")])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)

    with pytest.raises(RuntimeError, match="500"):
        asyncio.run(
            module.run_data_search_prompt_agent(
                "夏のハワイ売上",
                None,
                delegated_user_access_token="delegated-token",
                fabric_connection_id="conn-id-123",
            )
        )

    assert len(openai_client.responses.calls) == 1, "Pass 2 must NOT be invoked on non-recoverable failure"


def test_run_data_search_prompt_agent_no_connection_id_skips_pass1(monkeypatch) -> None:
    """connection_id 未設定なら Pass 1 をスキップして Pass 2 直行する。"""
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2_only", output=[])
    openai_client = _FakeOpenAIClient([pass2_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)
    monkeypatch.setattr(module, "_run_function_call_loop", _make_fake_function_call_loop(pass2_response))

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="",
        )
    )

    assert result is pass2_response
    assert len(openai_client.responses.calls) == 1
    assert openai_client.responses.calls[0]["tool_choice"] == "required"


def test_run_data_search_prompt_agent_requires_delegated_token(monkeypatch) -> None:
    """delegated token 不在は ValueError で fail-fast する。"""
    _patch_common(monkeypatch)

    with pytest.raises(ValueError, match="delegated"):
        asyncio.run(
            module.run_data_search_prompt_agent(
                "夏のハワイ売上",
                None,
                delegated_user_access_token="",
                fabric_connection_id="conn-id-123",
            )
        )


def test_run_data_search_prompt_agent_requires_project_endpoint(monkeypatch) -> None:
    """AZURE_AI_PROJECT_ENDPOINT 不在は ValueError で fail-fast する。"""
    settings_no_endpoint = _settings()
    settings_no_endpoint["project_endpoint"] = ""
    monkeypatch.setattr(module, "get_settings", lambda: settings_no_endpoint)
    monkeypatch.setattr(module, "DefaultAzureCredential", lambda: object())

    with pytest.raises(ValueError, match="AZURE_AI_PROJECT_ENDPOINT"):
        asyncio.run(
            module.run_data_search_prompt_agent(
                "夏のハワイ売上",
                None,
                delegated_user_access_token="delegated-token",
                fabric_connection_id="conn-id-123",
            )
        )


def test_is_recoverable_pass1_failure_classification() -> None:
    """recoverable error 判定の基本ケース。"""
    assert module._is_recoverable_pass1_failure(RuntimeError("401 Unauthorized"))
    assert module._is_recoverable_pass1_failure(RuntimeError("HTTP 403 Forbidden"))
    assert module._is_recoverable_pass1_failure(RuntimeError("OBO token failure"))
    assert module._is_recoverable_pass1_failure(RuntimeError("connection not found"))
    # rubber-duck `pr3-impl-review` Blocking #1: 400 / invalid_request_error は recoverable
    assert module._is_recoverable_pass1_failure(
        RuntimeError(
            "Error code: 400 - {'error': {'message': \"Invalid type for 'extra_body.tool_choice'.\", 'type': 'invalid_request_error'}}"
        )
    )
    assert module._is_recoverable_pass1_failure(
        RuntimeError("400 Bad Request: tool_choice shape mismatch")
    )
    # rubber-duck `tca-serialize-fix` Blocking #2: client-side JSON serialize failure
    # (Pydantic obj が extra_body に紛れ込んだ場合) も Pass 2 に降格する。
    assert module._is_recoverable_pass1_failure(
        TypeError("Object of type ToolChoiceAllowed is not JSON serializable")
    )
    assert not module._is_recoverable_pass1_failure(RuntimeError("Internal Server Error 500"))
    assert not module._is_recoverable_pass1_failure(RuntimeError("502 Bad Gateway"))


def test_run_data_search_prompt_agent_pass1_400_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で 400 invalid_request_error が出たら Pass 2 に降格する。

    rubber-duck `pr3-impl-review` Blocking #1: 未検証の `ToolChoiceAllowed.tools=[{...}]`
    shape を Foundry が拒否したケースを Pass 2 で吸収する保険を確認する。
    """
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2_after_400", output=[])
    bad_request = RuntimeError(
        "Error code: 400 - {'error': {'message': \"Invalid type for 'extra_body.tool_choice'.\", 'type': 'invalid_request_error'}}"
    )
    openai_client = _FakeOpenAIClient([bad_request, pass2_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)
    monkeypatch.setattr(module, "_run_function_call_loop", _make_fake_function_call_loop(pass2_response))

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    assert result is pass2_response
    assert len(openai_client.responses.calls) == 2, "Pass 2 must be invoked after 400 invalid_request_error"


def test_pass1_extra_body_tool_choice_is_json_serializable_dict(monkeypatch) -> None:
    """extra_body['tool_choice'] が JSON serialize 可能な plain dict であることを保証する。

    rubber-duck `tca-serialize-fix` Blocking #1: live で `Object of type ToolChoiceAllowed
    is not JSON serializable` 失敗を観測したため、Pydantic-like obj が extra_body に紛れ
    込んでいないか payload レベルで固定する (回帰防止)。
    """
    import json as _json

    _patch_common(monkeypatch)
    pass1_response = _build_response_with_fabric()
    openai_client = _FakeOpenAIClient([pass1_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)

    asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    pass1_call = openai_client.responses.calls[0]
    extra_body = pass1_call["extra_body"]
    tool_choice = extra_body["tool_choice"]
    assert isinstance(tool_choice, dict), (
        f"tool_choice must be a plain dict (not Pydantic obj), got {type(tool_choice).__name__}"
    )
    assert tool_choice.get("mode") == "required"
    assert tool_choice.get("tools") == [{"type": "fabric_dataagent_preview"}]
    assert tool_choice.get("type") == "allowed_tools", (
        "as_dict() output must include type='allowed_tools' for Foundry to recognize the shape"
    )
    # Critical: full extra_body must round-trip through json.dumps without TypeError
    _json.dumps(extra_body)


def test_pass1_serialize_typeerror_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で client-side JSON serialize 失敗 (TypeError) が出たら Pass 2 に降格する。

    rubber-duck `tca-serialize-fix` Blocking #2: as_dict() 修正後も SDK 内部の何か別の
    Pydantic obj が extra_body に紛れ込むケースを想定して、defense in depth で Pass 2
    fallback が効くことを固定する。
    """
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2_after_serialize", output=[])
    serialize_err = TypeError("Object of type ToolChoiceAllowed is not JSON serializable")
    openai_client = _FakeOpenAIClient([serialize_err, pass2_response])
    project_client = _FakeProjectClient(openai_client, "travel-data-search-gpt-5-4-mini")
    monkeypatch.setattr(module, "AIProjectClient", lambda endpoint, credential: project_client)
    monkeypatch.setattr(module, "_run_function_call_loop", _make_fake_function_call_loop(pass2_response))

    result = asyncio.run(
        module.run_data_search_prompt_agent(
            "夏のハワイ売上",
            None,
            delegated_user_access_token="delegated-token",
            fabric_connection_id="conn-id-123",
        )
    )

    assert result is pass2_response
    assert len(openai_client.responses.calls) == 2, (
        "Pass 2 must be invoked after client-side serialize TypeError"
    )


def test_detect_fabric_tool_invoked_handles_dict_and_object_outputs() -> None:
    """fabric tool 検出は dict / object 両方の output に対応する。"""
    obj_response = SimpleNamespace(output=[SimpleNamespace(type="fabric_dataagent_preview")])
    assert module._detect_fabric_tool_invoked(obj_response)

    dict_response = SimpleNamespace(output=[{"type": "fabric_dataagent_preview"}])
    assert module._detect_fabric_tool_invoked(dict_response)

    no_fabric = SimpleNamespace(output=[SimpleNamespace(type="message")])
    assert not module._detect_fabric_tool_invoked(no_fabric)

    empty_response = SimpleNamespace(output=[])
    assert not module._detect_fabric_tool_invoked(empty_response)


def test_build_data_search_agent_definition_with_fabric_connection() -> None:
    """connection_id ありなら Fabric tool が definition に含まれる。"""
    definition = module.build_data_search_agent_definition(
        "gpt-5-4-mini",
        fabric_connection_id="/subscriptions/sub/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/foundry/projects/proj/connections/fabric-conn",
    )
    assert definition is not None
    assert getattr(definition, "tools", None), "tools should be populated when fabric connection is set"


def test_build_data_search_agent_definition_without_fabric_connection() -> None:
    """connection_id 空なら Fabric tool は無く、CI も無効なら tools は空。"""
    definition = module.build_data_search_agent_definition(
        "gpt-5-4-mini",
        fabric_connection_id="",
        code_interpreter_enabled=False,
    )
    assert definition is not None
    tools = getattr(definition, "tools", []) or []
    assert tools == [], "tools must be empty without fabric or code interpreter"


def _make_fake_function_call_loop(final_response: Any):
    """`_run_function_call_loop` の fake (async) を作る。"""

    async def _fake(_openai_client, _initial, *, model_name: str):
        del model_name
        return final_response

    return _fake
