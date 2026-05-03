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


def _assert_pass2_payload_shape(call: dict[str, object]) -> None:
    """Foundry 400 regression guard (`Not allowed when agent is specified.`).

    rubber-duck `pass2-agent-ref-fix` Non-Blocking #2 反映: recoverable
    failure 全 4 経路 (zero-fabric / 401 / 400 invalid_request / serialize
    TypeError) で Pass 2 payload shape が壊れていないことを共通 helper で固定する。
    """
    assert call.get("tool_choice") == "required", "Pass 2 must force tool_choice=required"
    assert call.get("tools"), "Pass 2 must pass function tools at top level"
    extra_body = call.get("extra_body") or {}
    assert isinstance(extra_body, dict)
    assert (
        "agent_reference" not in extra_body
    ), "Pass 2 must NOT set extra_body.agent_reference (Foundry rejects agent + tools combination)"
    assert call.get(
        "instructions"
    ), "Pass 2 must pass instructions directly when agent_reference is absent"


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
    pass1_call = openai_client.responses.calls[0]
    # rubber-duck `tool-choice-required-fix` 反映 (live App Insights 2026-05-03):
    # Pass 1 は tool_choice="required" (top-level) + extra_body.agent_reference のみ。
    # 旧 ToolChoiceAllowed (extra_body.tool_choice={type:"allowed_tools", ...}) は
    # Foundry が `tool_choice.tools[0].type` で `file_search` 以外を拒否するため使わない。
    assert pass1_call["tool_choice"] == "required"
    assert pass1_call["extra_body"]["agent_reference"]["name"].startswith("travel-data-search-")
    assert (
        "tool_choice" not in pass1_call.get("extra_body", {})
    ), "Pass 1 must NOT set extra_body.tool_choice (Foundry rejects allowed_tools shape for fabric_dataagent_preview)"
    assert (
        "tools" not in pass1_call
    ), "Pass 1 must NOT pass top-level tools when agent_reference is set (Foundry rejects 'agent + tools')"


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
    _assert_pass2_payload_shape(pass2_call)


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
    _assert_pass2_payload_shape(openai_client.responses.calls[1])


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
    # rubber-duck `tool-choice-required-fix` defense-in-depth: 将来 Foundry が
    # tool_choice="required" + agent_reference の組み合わせを別 invalid_value 系で
    # reject する場合や、live App Insights で観測した
    # `Invalid value: 'fab...iew'. Value must be 'file_search'.` (param=`tool_choice.tools[0].type`)
    # 系の error が再発した場合に Pass 2 fallback で吸収する。
    assert module._is_recoverable_pass1_failure(
        RuntimeError(
            "Error code: 400 - {'error': {'message': \"Invalid value: 'fab...iew'. Value must be 'file_search'.\", "
            "'type': 'invalid_request_error', 'param': 'tool_choice.tools[0].type', 'code': 'invalid_value'}}"
        )
    )
    # rubber-duck `tca-serialize-fix` Blocking #2: client-side JSON serialize failure
    # (Pydantic obj が extra_body に紛れ込んだ場合) も Pass 2 に降格する (defense in depth)。
    assert module._is_recoverable_pass1_failure(
        TypeError("Object of type ToolChoiceAllowed is not JSON serializable")
    )
    assert not module._is_recoverable_pass1_failure(RuntimeError("Internal Server Error 500"))
    assert not module._is_recoverable_pass1_failure(RuntimeError("502 Bad Gateway"))


def test_run_data_search_prompt_agent_pass1_400_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で 400 invalid_request_error が出たら Pass 2 に降格する。

    rubber-duck `tool-choice-required-fix` 反映: live App Insights で観測した
    `Invalid value: 'fab...iew'. Value must be 'file_search'.` (invalid_value,
    param `tool_choice.tools[0].type`) と、過去の `Invalid type for 'extra_body.tool_choice'`
    の両方を defense-in-depth で recoverable と扱い、Pass 2 で吸収する。
    """
    _patch_common(monkeypatch)
    pass2_response = SimpleNamespace(id="resp_pass2_after_400", output=[])
    bad_request = RuntimeError(
        "Error code: 400 - {'error': {'message': \"Invalid value: 'fab...iew'. Value must be 'file_search'.\", "
        "'type': 'invalid_request_error', 'param': 'tool_choice.tools[0].type', 'code': 'invalid_value'}}"
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
    _assert_pass2_payload_shape(openai_client.responses.calls[1])


def test_pass1_payload_uses_tool_choice_required_top_level(monkeypatch) -> None:
    """Pass 1 が tool_choice="required" を top-level で渡し、extra_body には agent_reference のみ含むことを保証する。

    rubber-duck `tool-choice-required-fix` 反映: 旧 ToolChoiceAllowed
    (extra_body.tool_choice={type:"allowed_tools", ...}) は Foundry が
    `tool_choice.tools[0].type` で `file_search` 以外を拒否する (live App Insights
    2026-05-03 13:13/13:20 UTC で 3 件連続観測)。新形は
    - tool_choice: "required" (top-level, plain string)
    - extra_body: {"agent_reference": {...}} のみ
    で、agent definition に MicrosoftFabricPreviewTool だけ登録されている前提に
    依存する (live agent travel-data-search-gpt-5-4-mini:1 は Fabric only)。
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
    assert pass1_call["tool_choice"] == "required", (
        "Pass 1 must use tool_choice=required (string) — Foundry rejects allowed_tools shape "
        "for fabric_dataagent_preview"
    )
    extra_body = pass1_call["extra_body"]
    assert "agent_reference" in extra_body
    assert extra_body["agent_reference"]["type"] == "agent_reference"
    assert "tool_choice" not in extra_body, (
        "Pass 1 must NOT set extra_body.tool_choice — must be top-level"
    )
    assert "tools" not in pass1_call, (
        "Pass 1 must NOT pass top-level tools (Foundry rejects 'Not allowed when agent is specified.')"
    )
    # Critical: full kwargs must round-trip through json.dumps without TypeError
    _json.dumps({"extra_body": extra_body, "tool_choice": pass1_call["tool_choice"]})


def test_pass1_serialize_typeerror_falls_back_to_pass2(monkeypatch) -> None:
    """Pass 1 で client-side JSON serialize 失敗 (TypeError) が出たら Pass 2 に降格する。

    rubber-duck `tool-choice-required-fix` defense-in-depth: 新形では
    extra_body は `{"agent_reference": {...}}` の plain dict のみで TypeError 発生
    確率は激減するが、SDK 内部の future drift / 第三者拡張で Pydantic obj が
    紛れ込んだ場合の保険として fallback が効くことを固定する。
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
    _assert_pass2_payload_shape(openai_client.responses.calls[1])


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
