"""Foundry Prompt Agent 実行ラッパー。"""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, TypedDict

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition, WebSearchTool
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from src.agents.marketing_plan import INSTRUCTIONS as MARKETING_PLAN_INSTRUCTIONS
from src.config import get_settings
from src.model_deployments import resolve_model_deployment

logger = logging.getLogger(__name__)

_AGENT_NAME_SANITIZER = re.compile(r"[^a-z0-9-]+")
_SOURCE_LABELS = {
    "meeting_notes": "会議メモ",
    "emails": "メール",
    "teams_chats": "Teams チャット",
    "documents_notes": "ドキュメント / ノート",
}
_WORK_IQ_CONNECTION_NAME = "WorkIQCopilot"
_WORK_IQ_SERVER_LABEL = "mcp_M365Copilot"
_WORK_IQ_SERVER_DESCRIPTION = (
    "Microsoft 365 workplace context tools for organizational emails, meetings, chats, and documents."
)
_WORK_IQ_BASELINE_GUIDANCE = (
    "\n\n## Work IQ / Microsoft 365 tools の利用方針\n"
    "- Work IQ / Microsoft 365 MCP tool が利用可能な場合は、"
    "社内方針・過去施策・承認条件・会議メモ・メール・チャット・文書の確認にそれらを優先利用してください。\n"
    "- ツールから得た情報は、個人情報や長い原文を転載せず、企画判断に必要な要点だけを要約して反映してください。\n"
    "- この実行で Work IQ MCP tool が有効な場合は、少なくとも一度は Work IQ を参照してから企画書を作成してください。\n"
    "- Work IQ MCP tool が利用できない場合は、推測で続行せず失敗として扱ってください。\n"
    "- Work IQ で社内文脈を確認した後は、最新の旅行市場トレンド・競合情報の根拠を取るため "
    "**web_search ツールを最低 1 回は呼び出して** ください。"
    "Work IQ だけで企画書を完結させることは禁止です（社内文脈に偏り、市場根拠が古くなるため）。"
)


class WorkIQPromptConfig(TypedDict):
    """Prompt Agent に渡す Work IQ tool 構成。"""

    enabled: bool
    source_scope: list[str]


class WorkIQConnectionConfig(TypedDict):
    """Work IQ RemoteTool connection から復元した最小構成。"""

    connection_name: str
    server_url: str


def _build_marketing_plan_web_search_tool() -> WebSearchTool:
    """marketing-plan で使う Web Search tool を生成する。"""
    return WebSearchTool(
        user_location={"country": "JP", "region": "Tokyo"},
        search_context_size="medium",
    )


def _normalize_agent_name_token(value: str) -> str:
    """Prompt Agent 名に使えるトークンへ正規化する。"""
    lowered = value.strip().lower().replace(".", "-").replace("_", "-")
    normalized = _AGENT_NAME_SANITIZER.sub("-", lowered).strip("-")
    return normalized or "default"


def _resolve_marketing_plan_agent_name(model_name: str) -> str:
    """marketing-plan 用 Prompt Agent 名を解決する。"""
    settings = get_settings()
    base_name = settings["marketing_plan_prompt_agent_name"].strip() or "travel-marketing-plan"
    return f"{base_name}-{_normalize_agent_name_token(model_name)}"


def build_marketing_plan_agent_definition(
    model_name: str,
    *,
    work_iq_tool: MCPTool | None = None,
) -> PromptAgentDefinition:
    """marketing-plan 用の事前作成済み Agent 定義を返す。"""
    tools: list[object] = [_build_marketing_plan_web_search_tool()]
    if work_iq_tool is not None:
        tools.append(work_iq_tool)
    return PromptAgentDefinition(
        model=model_name,
        instructions=f"{MARKETING_PLAN_INSTRUCTIONS}{_WORK_IQ_BASELINE_GUIDANCE}",
        tools=tools,
    )


def _utc_now_iso() -> str:
    """UTC 現在時刻を ISO 文字列で返す。"""
    return datetime.now(timezone.utc).isoformat()


def _get_marketing_plan_agent(project_client: AIProjectClient, model_name: str):
    """marketing-plan 用 Prompt Agent を取得する。"""
    agent_name = _resolve_marketing_plan_agent_name(model_name)
    try:
        return project_client.agents.get(agent_name=agent_name)
    except ResourceNotFoundError:
        raise ValueError(
            "marketing-plan Foundry Agent が未作成です。scripts/postprovision.py を実行して "
            f"{agent_name} を同期してください"
        ) from None


def _resolve_work_iq_server_url(connection_target: object) -> str:
    """Work IQ RemoteTool connection から server_url を抽出する。"""
    if not isinstance(connection_target, str):
        return ""
    return connection_target.strip()


def _resolve_work_iq_connection(project_client: AIProjectClient) -> WorkIQConnectionConfig | None:
    """Foundry project の Work IQ RemoteTool connection 情報を返す。"""
    try:
        connections = list(project_client.connections.list())
    except Exception:
        return None

    for connection in connections:
        connection_name = getattr(connection, "name", "")
        connection_type = getattr(connection, "type", "")
        connection_target = _resolve_work_iq_server_url(getattr(connection, "target", ""))
        if not isinstance(connection_name, str) or not isinstance(connection_type, str):
            continue
        if connection_type != "RemoteTool" or not connection_target:
            continue
        if connection_name != _WORK_IQ_CONNECTION_NAME and _WORK_IQ_SERVER_LABEL not in connection_target:
            continue
        return {
            "connection_name": connection_name,
            "server_url": connection_target,
        }
    return None


def _build_work_iq_mcp_tool(project_client: AIProjectClient) -> MCPTool | None:
    """Foundry project の Work IQ Copilot connection から MCP tool を組み立てる。"""
    connection = _resolve_work_iq_connection(project_client)
    if connection is None:
        return None
    return MCPTool(
        server_label=_WORK_IQ_SERVER_LABEL,
        server_url=connection["server_url"],
        project_connection_id=connection["connection_name"],
        require_approval="never",
    )


def sync_marketing_plan_agent(project_endpoint: str, model_name: str) -> bool:
    """marketing-plan 用 Prompt Agent を create_version で同期する。"""
    project_client: AIProjectClient | None = None
    try:
        settings = dict(get_settings())
        if not settings.get("project_endpoint", "").strip():
            settings["project_endpoint"] = project_endpoint
        model_name = resolve_model_deployment(model_name, settings=settings)  # type: ignore[arg-type]
        project_client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
        agent_name = _resolve_marketing_plan_agent_name(model_name)
        work_iq_tool = _build_work_iq_mcp_tool(project_client)
        project_client.agents.create_version(
            agent_name=agent_name,
            definition=build_marketing_plan_agent_definition(model_name, work_iq_tool=work_iq_tool),
        )
        logger.info("marketing-plan Prompt Agent を同期しました: %s", agent_name)
        return True
    finally:
        close_method = getattr(project_client, "close", None)
        if callable(close_method):
            close_method()


def _build_work_iq_tool_guidance(
    config: WorkIQPromptConfig,
) -> str:
    """Work IQ MCP tool 利用時の追加指示を構築する。"""
    source_labels = [_SOURCE_LABELS.get(scope, scope) for scope in config["source_scope"] if scope]
    selected_sources = "、".join(source_labels) if source_labels else "Microsoft 365"
    return (
        "Work IQ MCP + Web Search 利用ガイド:\n"
        f"- 選択された職場ソース（{selected_sources}）に関係する追加文脈を確認するため、Work IQ MCP tool を優先利用してください。\n"
        "- まず Work IQ から、過去の会議・メール・チャット・社内文書にある方針、制約、過去施策、承認条件を高レベルに把握してください。\n"
        "- 原文の長い引用や個人情報の転載は避け、企画判断に必要な要点だけを要約して利用してください。\n"
        "- この実行では Work IQ MCP tool を少なくとも一度は参照してから企画書を作成してください。\n"
        "- Work IQ MCP tool が使えない場合は、推測で続行せずエラーとして終了してください。\n"
        "- Work IQ で社内文脈を整理した後は、必ず Web Search ツールを最低 1 回は呼び出して、最新の旅行市場トレンド・競合情報・観光統計・公式情報を確認してください。\n"
        "- Web Search で得た最新情報は、企画書の「販促チャネル」「差別化ポイント」「KPI」セクションに反映してください。\n"
        "- Web Search を一度も呼び出さずに企画書を仕上げることは禁止です。社内文脈だけでは市場・競合状況を担保できないため、必ず外部情報源で裏付けを取ってください。"
    )


def _build_marketing_plan_responses_web_search_tool() -> dict[str, object]:
    """Responses API で使う Web Search tool 定義を返す。"""
    return {
        "type": "web_search",
        "user_location": {"type": "approximate", "country": "JP", "region": "Tokyo"},
        "search_context_size": "medium",
    }


def _build_work_iq_responses_tool(
    server_url: str,
    *,
    connection_name: str,
) -> dict[str, object]:
    """Responses API で Work IQ MCP を呼ぶための tool 定義を返す。"""
    return {
        "type": "mcp",
        "server_label": _WORK_IQ_SERVER_LABEL,
        "server_url": server_url,
        "project_connection_id": connection_name,
        "require_approval": "never",
        "server_description": _WORK_IQ_SERVER_DESCRIPTION,
    }


def _build_work_iq_tool_choice() -> dict[str, str]:
    """Responses API に Work IQ MCP を最低 1 回使わせる tool_choice を返す。"""
    return {"type": "mcp", "server_label": _WORK_IQ_SERVER_LABEL}


def _detect_marketing_plan_tool_usage(response: object) -> tuple[bool, bool]:
    """Foundry Responses API の output から Work IQ / Web Search 呼び出しを検出する。

    返り値: (work_iq_called, web_search_called)

    Bug D rubber-duck NB#1 (2026-05-03): chat.py:_TOOL_CALL_TYPE_MAP は
    `bing_grounding_call` も Web Search にマップしているため両方を正規化検出する。
    また、output が nested (item.output / item.contents) になる SDK preview 形状にも
    対応するため queue ベースで再帰的に走査する。
    """
    work_iq_called = False
    web_search_called = False

    def _value(item: object, attr: str) -> object:
        if isinstance(item, dict):
            return item.get(attr)
        return getattr(item, attr, None)

    queue: list[object] = []
    initial_output = getattr(response, "output", None)
    if isinstance(initial_output, list):
        queue.extend(initial_output)

    while queue:
        item = queue.pop(0)
        if isinstance(item, list):
            queue[:0] = item
            continue
        item_type_raw = _value(item, "type") or ""
        item_type = str(item_type_raw)
        server_label_raw = _value(item, "server_label") or ""
        server_label = str(server_label_raw)
        if item_type:
            if item_type == "mcp_call" or item_type.startswith("mcp_"):
                if not server_label or server_label == _WORK_IQ_SERVER_LABEL:
                    work_iq_called = True
            elif (
                item_type == "web_search_call"
                or item_type.startswith("web_search")
                or item_type == "bing_grounding_call"
                or item_type.startswith("bing_grounding")
            ):
                web_search_called = True

        nested_output = _value(item, "output")
        if isinstance(nested_output, list):
            queue.extend(nested_output)
        nested_contents = _value(item, "contents")
        if isinstance(nested_contents, list):
            queue.extend(nested_contents)

    return work_iq_called, web_search_called


def run_marketing_plan_prompt_agent(
    user_input: str,
    model_settings: dict | None = None,
    *,
    work_iq: WorkIQPromptConfig | None = None,
    work_iq_access_token: str = "",
) -> object:
    """Foundry Prompt Agent として marketing-plan-agent を実行する。"""
    settings = get_settings()
    project_endpoint = settings["project_endpoint"].strip()
    if not project_endpoint:
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT が未設定です")

    model_name = resolve_model_deployment(settings["model_name"], settings=settings)
    if model_settings and isinstance(model_settings.get("model"), str) and model_settings["model"].strip():
        model_name = resolve_model_deployment(model_settings["model"].strip(), settings=settings)

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = None
    try:
        work_iq_config = work_iq or {"enabled": False, "source_scope": []}
        agent = _get_marketing_plan_agent(project_client, model_name)
        if work_iq_config["enabled"]:
            access_token = work_iq_access_token.strip()
            if not access_token:
                raise ValueError("Work IQ is enabled for the Foundry marketing-plan path, but no delegated access token was supplied.")
            work_iq_connection = _resolve_work_iq_connection(project_client)
            if work_iq_connection is None:
                raise ValueError(
                    "Work IQ is enabled for the Foundry marketing-plan path, but no WorkIQCopilot RemoteTool connection was found."
                )
            response_kwargs: dict[str, object] = {
                "model": model_name,
                "input": (
                    f"{_build_work_iq_tool_guidance(work_iq_config)}"
                    f"\n\n---\n\nユーザー入力:\n{user_input}"
                ),
                "extra_body": {
                    "agent_reference": {"name": agent.name, "type": "agent_reference"},
                    "tool_choice": _build_work_iq_tool_choice(),
                },
            }
            openai_client = project_client.get_openai_client(api_key=access_token)
        else:
            response_kwargs = {
                "model": model_name,
                "input": user_input,
                "extra_body": {"agent_reference": {"name": agent.name, "type": "agent_reference"}},
            }
            openai_client = project_client.get_openai_client()
        response = openai_client.responses.create(
            **response_kwargs,
        )
        if work_iq_config["enabled"]:
            work_iq_called, web_search_called = _detect_marketing_plan_tool_usage(response)
            if not web_search_called:
                # Bug D fix 2026-05-03: Work IQ enabled でも Web Search が呼ばれないと
                # 企画書の市場・競合根拠が社内文脈に偏る。tool_choice=mcp は Work IQ
                # 強制を維持しつつ、Web Search 不在を観測ログとして残してデモ品質を
                # 計測可能にする。tool_choice="required" は LLM が WorkIQ を skip する
                # リスクが上がるため使わない。
                logger.warning(
                    "marketing-plan Foundry path で Web Search tool が呼ばれませんでした (work_iq_called=%s)。"
                    "instructions で必ず 1 回は呼び出すよう指示しているため、モデルの compliance ログとして記録。",
                    work_iq_called,
                )
            else:
                logger.info(
                    "marketing-plan Foundry path で Work IQ + Web Search 両方を確認 (work_iq_called=%s, web_search_called=%s)",
                    work_iq_called,
                    web_search_called,
                )
        return response
    finally:
        close_openai = getattr(openai_client, "close", None)
        if callable(close_openai):
            close_openai()
        close_project = getattr(project_client, "close", None)
        if callable(close_project):
            close_project()


# ===========================================================================
# data-search-agent Foundry Prompt Agent (PR 3)
# ===========================================================================
# rubber-duck v3 GO 反映: 2-pass tool_choice (Pass 1 Fabric only / Pass 2 function tool fallback)
# + bounded ThreadPoolExecutor + circuit-open + lazy import of preview SDK classes.

_DATA_SEARCH_BASELINE_GUIDANCE = (
    "\n\n## Fabric Data Agent / Microsoft Fabric IQ の利用方針\n"
    "- 売上 / 予約 / レビュー / 顧客分布 / 季節トレンドの問い合わせは、必ず Microsoft Fabric Data Agent (`fabric_dataagent_preview`) を最初に呼び出してください。\n"
    "- Fabric Data Agent は travelIQ_v2 オントロジー (`lh_travel_marketing_v2` lakehouse) に紐付いており、ユーザの ID を On-Behalf-Of で引き継いで実データを取得できます。\n"
    "- Fabric Data Agent が回答を返せない、または認可エラー (401/403) の場合のみ `search_sales_history` / `search_customer_reviews` 関数ツールにフォールバックしてください。\n"
    "- 関数ツールは Fabric SQL endpoint への直接フォールバック経路で、Fabric Data Agent 経路よりも限定的なクエリ機能しか持ちません。\n"
    "- 情報が見つからないときは想像で補完せず、`データ取得不可` と明示してから一般的な分析方針を提示してください。"
)


class _DataSearchToolDispatch(TypedDict):
    """function tool 呼び出し結果を Foundry に返すための minimal payload。"""

    call_id: str
    output: str


def _resolve_data_search_agent_name(model_name: str) -> str:
    """data-search 用 Prompt Agent 名を解決する。"""
    settings = get_settings()
    base_name = settings.get("data_search_prompt_agent_name", "").strip() or "travel-data-search"
    return f"{base_name}-{_normalize_agent_name_token(model_name)}"


def _build_data_search_instructions() -> str:
    """data-search Prompt Agent 用の instructions 文字列を組み立てる。

    rubber-duck `pass2-agent-ref-fix` Suggestion #1 反映: agent definition と
    Pass 2 fallback の両方が同じ instructions を使うことを保証する (drift 防止)。
    """
    from src.agents.data_search import INSTRUCTIONS as DATA_SEARCH_INSTRUCTIONS

    return f"{DATA_SEARCH_INSTRUCTIONS}{_DATA_SEARCH_BASELINE_GUIDANCE}"


def _build_data_search_function_tools() -> list[dict[str, object]]:
    """Foundry Responses API 用の function tool 定義を返す（Pass 2 fallback）。"""
    return [
        {
            "type": "function",
            "name": "search_sales_history",
            "description": "Fabric SQL endpoint の sales 履歴をフィルタ条件付きで検索する。Fabric Data Agent が利用不能なときのフォールバック専用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "自然言語の検索クエリ"},
                    "season": {
                        "type": "string",
                        "description": "季節フィルタ (spring/summer/autumn/winter)",
                        "enum": ["spring", "summer", "autumn", "winter"],
                    },
                    "region": {"type": "string", "description": "地域フィルタ（例: 沖縄、ハワイ）"},
                },
                "required": ["query"],
            },
        },
        {
            "type": "function",
            "name": "search_customer_reviews",
            "description": "Fabric SQL endpoint の顧客レビューを検索する。Fabric Data Agent が利用不能なときのフォールバック専用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_name": {"type": "string", "description": "プラン名でのフィルタ"},
                    "min_rating": {
                        "type": "integer",
                        "description": "最低評価 (1〜5)",
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": [],
            },
        },
    ]


def build_data_search_agent_definition(
    model_name: str,
    *,
    fabric_connection_id: str,
    code_interpreter_enabled: bool = False,
) -> PromptAgentDefinition:
    """data-search 用の事前作成済み Agent 定義を返す。

    rubber-duck v2 落とし穴 #5: preview SDK class (MicrosoftFabricPreviewTool) を
    関数内で lazy import し、preview API の import 失敗で app 全体起動失敗にしない。
    """
    tools: list[object] = []
    if fabric_connection_id.strip():
        try:
            from azure.ai.projects.models import (
                FabricDataAgentToolParameters,
                MicrosoftFabricPreviewTool,
                ToolProjectConnection,
            )

            tools.append(
                MicrosoftFabricPreviewTool(
                    fabric_dataagent_preview=FabricDataAgentToolParameters(
                        project_connections=[
                            ToolProjectConnection(project_connection_id=fabric_connection_id.strip())
                        ]
                    )
                )
            )
        except ImportError as exc:
            logger.warning(
                "MicrosoftFabricPreviewTool が import できません: %s — Fabric tool 抜きで agent 定義を作成",
                exc,
            )

    if code_interpreter_enabled:
        try:
            from azure.ai.projects.models import CodeInterpreterTool

            tools.append(CodeInterpreterTool())
        except ImportError as exc:
            logger.info("CodeInterpreterTool が import できません（スキップ）: %s", exc)

    return PromptAgentDefinition(
        model=model_name,
        instructions=_build_data_search_instructions(),
        tools=tools,
    )


def _get_data_search_agent(project_client: AIProjectClient, model_name: str):
    """data-search 用 Prompt Agent を取得する。"""
    agent_name = _resolve_data_search_agent_name(model_name)
    try:
        return project_client.agents.get(agent_name=agent_name)
    except ResourceNotFoundError:
        raise ValueError(
            "data-search Foundry Agent が未作成です。scripts/postprovision.py を実行して "
            f"{agent_name} を同期してください"
        ) from None


def sync_data_search_agent(project_endpoint: str, model_name: str) -> bool:
    """data-search 用 Prompt Agent を create_version で同期する。"""
    project_client: AIProjectClient | None = None
    try:
        settings = dict(get_settings())
        if not settings.get("project_endpoint", "").strip():
            settings["project_endpoint"] = project_endpoint
        model_name = resolve_model_deployment(model_name, settings=settings)  # type: ignore[arg-type]
        project_client = AIProjectClient(endpoint=project_endpoint, credential=DefaultAzureCredential())
        agent_name = _resolve_data_search_agent_name(model_name)
        fabric_connection_id = str(settings.get("foundry_fabric_connection_id", "") or "").strip()
        if not fabric_connection_id:
            logger.info(
                "FOUNDRY_FABRIC_CONNECTION_ID 未設定のため data-search Prompt Agent を Fabric tool 抜きで同期します: %s",
                agent_name,
            )
        ci_enabled = str(settings.get("enable_code_interpreter", "")).strip().lower() in {"true", "1", "yes"}
        project_client.agents.create_version(
            agent_name=agent_name,
            definition=build_data_search_agent_definition(
                model_name,
                fabric_connection_id=fabric_connection_id,
                code_interpreter_enabled=ci_enabled,
            ),
        )
        logger.info("data-search Prompt Agent を同期しました: %s", agent_name)
        return True
    finally:
        close_method = getattr(project_client, "close", None)
        if callable(close_method):
            close_method()


def _detect_fabric_tool_invoked(response: Any) -> bool:
    """response.output から Fabric tool 呼び出しの有無を検出する。

    rubber-duck v2 落とし穴 #6: preview SDK の output type drift に耐えるため、
    `_TOOL_CALL_TYPE_MAP` 等には依存せず raw output を走査する。
    """
    output = getattr(response, "output", None)
    if not output:
        return False
    for item in output:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if not isinstance(item_type, str):
            continue
        if "fabric" in item_type.lower() or "fabric_dataagent" in item_type.lower():
            return True
    return False


def _is_recoverable_pass1_failure(exc: Exception) -> bool:
    """Pass 1 で recoverable な失敗（→ Pass 2 へ降格）かを判定する。

    OBO 401/403 / connection misconfig / Fabric tool unavailable / 400 系 client error
    （未検証 ToolChoiceAllowed shape の Foundry rejection 等）のみ Pass 2 にする。
    5xx / 一般 exception は Pass 2 にせず fail loud。
    """
    message = str(exc).lower()
    if "401" in message or "403" in message:
        return True
    if "obo" in message or "user_impersonation" in message:
        return True
    if "tool_user_error" in message and "ara obo" in message:
        return True
    if "connection" in message and ("not found" in message or "invalid" in message):
        return True
    if "fabric_dataagent_preview" in message and "not supported" in message:
        return True
    # rubber-duck `pr3-impl-review` Blocking #1 反映: ToolChoiceAllowed.tools=[{...}] が
    # live API で 400 を返すケースを Pass 2 で吸収する保険を効かせる。
    # rubber-duck `pr3-blocker-fix-final` Non-blocking #3 反映: invalid_request_error 単独
    # では fail-loud invariant を弱めすぎるため、tool_choice / fabric / extra_body マーカー
    # との AND に narrow する。
    is_400 = "400" in message
    is_invalid_request = "invalid_request_error" in message
    has_known_marker = any(
        marker in message
        for marker in (
            "tool_choice",
            "toolchoiceallowed",
            "allowed_tools",
            "fabric_dataagent_preview",
            "fabric",
            "extra_body",
        )
    )
    if is_invalid_request and has_known_marker:
        return True
    if is_400 and ("bad request" in message or has_known_marker):
        return True
    # Client-side JSON serialize failure (Pydantic obj slipped into extra_body).
    # Live で `Object of type ToolChoiceAllowed is not JSON serializable` を観測したため
    # Pass 2 に降格して Fabric/SQL fallback へつなぐ保険。`fabric` marker は広すぎるので
    # `toolchoice` / `allowed_tools` に限定する。
    if "json serializable" in message and (
        "toolchoice" in message or "allowed_tools" in message
    ):
        return True
    return False


def _extract_function_calls(response: Any) -> list[dict[str, Any]]:
    """response.output から function_call output を抽出する。"""
    output = getattr(response, "output", None)
    if not output:
        return []
    function_calls: list[dict[str, Any]] = []
    for item in output:
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type != "function_call":
            continue
        call_id = getattr(item, "call_id", None) or (item.get("call_id") if isinstance(item, dict) else None)
        name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
        arguments = getattr(item, "arguments", None) or (item.get("arguments") if isinstance(item, dict) else None)
        if not call_id or not name:
            continue
        function_calls.append({"call_id": call_id, "name": name, "arguments": arguments or "{}"})
    return function_calls


_KNOWN_DATA_SEARCH_FUNCTIONS = {"search_sales_history", "search_customer_reviews"}
_FUNCTION_CALL_LOOP_MAX_ITER = 8
_FUNCTION_CALL_PER_TOOL_TIMEOUT_SECONDS = 30.0
_FUNCTION_CALL_LOOP_TOTAL_TIMEOUT_SECONDS = 120.0


async def _dispatch_data_search_function_call(
    name: str,
    arguments_json: str,
) -> _DataSearchToolDispatch:
    """known function name のみ dispatch し、Foundry に返す output を作る。

    rubber-duck `pr3-impl-review` Blocking #2 反映: 既存 UI 互換のため、各 function call は
    canonical tool name (`search_sales_history` / `search_customer_reviews`) で
    `trace_tool_invocation` 経由 running → completed/failed の lifecycle event を発火する。

    rubber-duck `pr3-blocker-fix-final` Blocking #1 反映: sync helper の戻り値から
    `source="fabric"` のときに evidence event を発行する。frontend の `iq-brand.ts` は
    `search_*` tool を `event.evidence` の source ベースで `fabric_iq` 分類するため、
    evidence なしでは Pass 2 success が `null` ブランドに落ちてしまう (= Fabric IQ chip
    が出ない)。これを防ぐ。
    """
    from src.agents.data_search import (
        _get_fallback_executor,
        _get_fallback_semaphore,
        _record_fallback_timeout,
        _SyncSearchResult,
        emit_review_evidence_for_sync,
        emit_sales_evidence_for_sync,
        search_customer_reviews_sync,
        search_sales_history_sync,
    )
    from src.tool_telemetry import trace_tool_invocation

    try:
        arguments = json.loads(arguments_json) if arguments_json else {}
    except (ValueError, TypeError):
        arguments = {}

    if name not in _KNOWN_DATA_SEARCH_FUNCTIONS:
        logger.warning("data-search Pass 2: 未知の function name (%s) — error output を返す", name)
        return {
            "call_id": "",
            "output": json.dumps({"error": f"Unknown function: {name}"}, ensure_ascii=False),
        }

    executor = _get_fallback_executor()
    semaphore = _get_fallback_semaphore()

    async def _run_sync_in_executor(func, /, **kwargs):
        loop = asyncio.get_running_loop()
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    loop.run_in_executor(executor, lambda: func(**kwargs)),
                    timeout=_FUNCTION_CALL_PER_TOOL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                _record_fallback_timeout()
                raise

    output: str | None = None
    sync_result: _SyncSearchResult | None = None
    season_arg = arguments.get("season")
    region_arg = arguments.get("region")
    plan_name_arg = arguments.get("plan_name")
    min_rating_arg = arguments.get("min_rating")
    try:
        async with trace_tool_invocation(
            name,
            agent_name="data-search-agent",
            source="fabric_sql",
            provider="fabric",
        ):
            if name == "search_sales_history":
                sync_result = await _run_sync_in_executor(
                    search_sales_history_sync,
                    query=str(arguments.get("query", "")),
                    season=season_arg,
                    region=region_arg,
                )
                output = sync_result["payload"]
            elif name == "search_customer_reviews":
                sync_result = await _run_sync_in_executor(
                    search_customer_reviews_sync,
                    plan_name=plan_name_arg,
                    min_rating=min_rating_arg,
                )
                output = sync_result["payload"]
    except asyncio.TimeoutError:
        output = json.dumps(
            {"error": f"Function {name} timed out after {_FUNCTION_CALL_PER_TOOL_TIMEOUT_SECONDS:.0f}s"},
            ensure_ascii=False,
        )
    except (ValueError, TypeError, OSError) as exc:
        logger.warning("data-search function tool 実行失敗: %s: %s", name, exc)
        output = json.dumps({"error": str(exc)}, ensure_ascii=False)

    # rubber-duck `pr3-blocker-fix-final`: sync helper が成功して Fabric SQL から取れた
    # ときだけ evidence event を発行 (frontend は evidence の source で fabric_iq 分類)。
    # local fallback (CSV) のときも evidence は出すが source="local" なので fabric_iq には
    # ならず、silent fallback の信号を消さない。
    if sync_result is not None:
        try:
            if name == "search_sales_history":
                emit_sales_evidence_for_sync(
                    sync_result,
                    season=season_arg if isinstance(season_arg, str) else None,
                    region=region_arg if isinstance(region_arg, str) else None,
                )
            elif name == "search_customer_reviews":
                emit_review_evidence_for_sync(
                    sync_result,
                    plan_name=plan_name_arg if isinstance(plan_name_arg, str) else None,
                    min_rating=min_rating_arg if isinstance(min_rating_arg, int) else None,
                )
        except Exception as exc:  # noqa: BLE001 - telemetry は best-effort
            logger.warning("data-search evidence event emission 失敗: %s: %s", name, exc)

    if output is None:
        output = json.dumps({"error": f"Function {name} returned no output"}, ensure_ascii=False)

    return {"call_id": "", "output": output}


async def _run_function_call_loop(
    openai_client: Any,
    initial_response: Any,
    *,
    model_name: str,
) -> Any:
    """Pass 2 の function-call continuation loop を実行する。

    rubber-duck v2 落とし穴 #6: known function name のみ dispatch、unknown は loop 抜ける + warn。
    全体に asyncio.wait_for(120s) を被せ、max iter=8 で hang 防止。
    """
    response = initial_response

    async def _loop_body() -> Any:
        nonlocal response
        for iteration in range(_FUNCTION_CALL_LOOP_MAX_ITER):
            function_calls = _extract_function_calls(response)
            if not function_calls:
                return response

            tool_outputs: list[dict[str, Any]] = []
            for call in function_calls:
                if call["name"] not in _KNOWN_DATA_SEARCH_FUNCTIONS:
                    logger.warning(
                        "data-search Pass 2: 未知の function name (%s) — loop 抜けます",
                        call["name"],
                    )
                    return response
                dispatch = await _dispatch_data_search_function_call(call["name"], call["arguments"])
                tool_outputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": call["call_id"],
                        "output": dispatch["output"],
                    }
                )

            previous_response_id = getattr(response, "id", None)
            if not previous_response_id:
                logger.warning("data-search Pass 2: response.id が空のため continuation 不能")
                return response
            response = await asyncio.to_thread(
                openai_client.responses.create,
                model=model_name,
                input=tool_outputs,
                previous_response_id=previous_response_id,
            )
            logger.info("data-search Pass 2 continuation iter=%d", iteration + 1)

        logger.warning("data-search Pass 2: max iter (%d) 到達", _FUNCTION_CALL_LOOP_MAX_ITER)
        return response

    return await asyncio.wait_for(_loop_body(), timeout=_FUNCTION_CALL_LOOP_TOTAL_TIMEOUT_SECONDS)


async def run_data_search_prompt_agent(
    user_input: str,
    model_settings: dict | None = None,
    *,
    delegated_user_access_token: str = "",
    fabric_connection_id: str = "",
    code_interpreter_enabled: bool = False,
) -> Any:
    """Foundry Prompt Agent として data-search-agent を実行する。

    2-pass 戦略:
    - Pass 1: agent_reference + tool_choice="required" で Fabric only 強制
      (agent definition の tools が `MicrosoftFabricPreviewTool` のみのため、
       required mode で必ず 1 個 tool が呼ばれる = 論理的に Fabric only)
    - Pass 2 (Pass 1 zero-fabric / 401 / 403 / connection misconfig 時のみ): function tool fallback
    - 5xx / 一般 exception: fail loud (Pass 2 に降格しない)
    """
    from src.agents.data_search import original_user_prompt_context
    from src.tool_telemetry import build_tool_event_data, emit_tool_event

    settings = get_settings()
    project_endpoint = settings["project_endpoint"].strip()
    if not project_endpoint:
        raise ValueError("AZURE_AI_PROJECT_ENDPOINT が未設定です")

    delegated_token = (delegated_user_access_token or "").strip()
    if not delegated_token:
        raise ValueError(
            "data-search Foundry Prompt Agent は delegated user access token が必須です（auth_mode=delegated 時のみ起動）。"
        )

    model_name = resolve_model_deployment(settings["model_name"], settings=settings)
    if model_settings and isinstance(model_settings.get("model"), str) and model_settings["model"].strip():
        model_name = resolve_model_deployment(model_settings["model"].strip(), settings=settings)

    credential = DefaultAzureCredential()
    project_client = AIProjectClient(endpoint=project_endpoint, credential=credential)
    openai_client = None
    try:
        agent = _get_data_search_agent(project_client, model_name)
        openai_client = project_client.get_openai_client(api_key=delegated_token)

        # Pass 1: Fabric tool only via ToolChoiceAllowed
        # rubber-duck `pr3-impl-review` Blocking #2 反映: UI 互換のため canonical
        # `query_data_agent` の running → completed/failed lifecycle event を発火する。
        # 補助 telemetry は `logger.info` で AppTraces に記録する (event 重複で UI に
        # spinner が残るのを防ぐ — `fabric_data_agent_invocation` event は廃止)。
        pass1_response = None
        pass1_failed_recoverable = False
        if fabric_connection_id.strip():
            emit_tool_event(
                build_tool_event_data(
                    "query_data_agent",
                    "running",
                    agent_name="data-search-agent",
                    source="fabric_data_agent",
                    provider="foundry",
                    phase="pass1",
                )
            )
            try:
                # rubber-duck `tool-choice-required-fix` 反映 (live App Insights
                # 2026-05-03 13:13 / 13:20 UTC で 3 件連続観測):
                # Foundry Responses API は
                #   tool_choice={"type":"allowed_tools","mode":"required",
                #                "tools":[{"type":"fabric_dataagent_preview"}]}
                # を 400 invalid_value で reject する (param=`tool_choice.tools[0].type`,
                # message="Value must be 'file_search'.")。tool_choice.allowed_tools.tools[].type
                # に許可されるのは現状 `file_search` のみで、preview hosted tool
                # (`fabric_dataagent_preview` 等) は allowed_tools 形式で named-allow できない。
                #
                # Microsoft Foundry docs (microsoft_docs_search 確認) でも tool_choice の
                # deterministic 制御値は `auto` / `required` / `none` のみと記載されている。
                # agent definition には MicrosoftFabricPreviewTool だけ登録しているので
                # (live agent travel-data-search-gpt-5-4-mini:1 の definition.tools で
                # 2026-05-03 確認済)、tool_choice="required" で
                # 「必ず何か 1 つ tool を呼ぶ」 = 「Fabric tool を呼ぶ」が論理的に保証される。
                #
                # 注意: ENABLE_CODE_INTERPRETER=true で agent に CodeInterpreterTool が
                # 同居登録されると、required mode では Fabric vs CodeInterpreter のどちらが
                # 選ばれるかが model 依存になる。現運用では false 固定なので問題ないが、
                # 有効化する場合は別 variant の Fabric-only agent を新設する設計に切り替えること。
                pass1_kwargs = {
                    "model": model_name,
                    "input": user_input,
                    "tool_choice": "required",
                    "extra_body": {
                        "agent_reference": {"name": agent.name, "type": "agent_reference"},
                    },
                }
                token_ctx = original_user_prompt_context(user_input)
                with token_ctx:
                    pass1_response = await asyncio.to_thread(openai_client.responses.create, **pass1_kwargs)

                fabric_invoked = _detect_fabric_tool_invoked(pass1_response)
                logger.info(
                    "fabric_data_agent_invocation pass=pass1 fabric_tool_invoked=%s status=%s",
                    fabric_invoked,
                    "completed" if fabric_invoked else "no_op",
                )
                if fabric_invoked:
                    emit_tool_event(
                        build_tool_event_data(
                            "query_data_agent",
                            "completed",
                            agent_name="data-search-agent",
                            source="fabric_data_agent",
                            provider="foundry",
                            phase="pass1",
                        )
                    )
                    return pass1_response
                logger.info("data-search Pass 1: Fabric tool が呼ばれませんでした → Pass 2 に降格")
                emit_tool_event(
                    build_tool_event_data(
                        "query_data_agent",
                        "failed",
                        agent_name="data-search-agent",
                        source="fabric_data_agent",
                        provider="foundry",
                        phase="pass1",
                        fallback="pass2_function_tools",
                        error_message="Fabric tool was not invoked by the model in Pass 1",
                    )
                )
                pass1_failed_recoverable = True
            except Exception as exc:
                if _is_recoverable_pass1_failure(exc):
                    logger.warning(
                        "data-search Pass 1: recoverable 失敗 → Pass 2 に降格: %s",
                        exc,
                    )
                    logger.info(
                        "fabric_data_agent_invocation pass=pass1 fabric_tool_invoked=False status=fallback error=%s",
                        str(exc)[:200],
                    )
                    emit_tool_event(
                        build_tool_event_data(
                            "query_data_agent",
                            "failed",
                            agent_name="data-search-agent",
                            source="fabric_data_agent",
                            provider="foundry",
                            phase="pass1",
                            fallback="pass2_function_tools",
                            error_message=str(exc)[:200],
                        )
                    )
                    pass1_failed_recoverable = True
                else:
                    logger.error("data-search Pass 1: non-recoverable 失敗: %s", exc)
                    emit_tool_event(
                        build_tool_event_data(
                            "query_data_agent",
                            "failed",
                            agent_name="data-search-agent",
                            source="fabric_data_agent",
                            provider="foundry",
                            phase="pass1",
                            error_message=str(exc)[:200],
                        )
                    )
                    raise
        else:
            logger.info("FOUNDRY_FABRIC_CONNECTION_ID 未設定 — Pass 1 スキップして Pass 2 直行")
            pass1_failed_recoverable = True

        # Pass 2: function tool fallback (`tool_choice="required"` + function-call loop)
        if not pass1_failed_recoverable:
            return pass1_response

        # rubber-duck round 3 (live App Insights 2026-05-03 12:41 UTC) 反映:
        # Foundry Responses API は extra_body.agent_reference (agent specified) と
        # top-level `tools` の併用を禁止する (`'Not allowed when agent is specified.'`,
        # code 'invalid_payload', param 'tools', HTTP 400)。Pass 2 では agent_reference
        # を bypass し、agent definition と同等の instructions / function tools を
        # kwargs に直接渡す。Pass 1 (agent_reference 経由 Fabric only) との役割分担は
        # 変わらない。
        # 副作用 (rubber-duck non-blocking #1): agent definition に登録された
        # CodeInterpreterTool は Pass 2 では使えない。fallback path は SQL endpoint
        # 経由の function tools のみで完結させる仕様とする。
        pass2_kwargs = {
            "model": model_name,
            "input": user_input,
            "instructions": _build_data_search_instructions(),
            "tools": _build_data_search_function_tools(),
            "tool_choice": "required",
        }
        with original_user_prompt_context(user_input):
            pass2_initial = await asyncio.to_thread(openai_client.responses.create, **pass2_kwargs)
            pass2_final = await _run_function_call_loop(
                openai_client,
                pass2_initial,
                model_name=model_name,
            )

        fabric_invoked_in_pass2 = _detect_fabric_tool_invoked(pass2_final)
        logger.info(
            "fabric_data_agent_invocation pass=pass2 fabric_tool_invoked=%s status=%s",
            fabric_invoked_in_pass2,
            "completed" if fabric_invoked_in_pass2 else "no_op",
        )
        return pass2_final
    finally:
        close_openai = getattr(openai_client, "close", None)
        if callable(close_openai):
            close_openai()
        close_project = getattr(project_client, "close", None)
        if callable(close_project):
            close_project()
