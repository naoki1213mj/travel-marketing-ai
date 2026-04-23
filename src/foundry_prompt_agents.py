"""Foundry Prompt Agent 実行ラッパー。"""

import logging
import re
from datetime import datetime, timezone
from typing import TypedDict

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition, WebSearchTool
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from src.agents.marketing_plan import INSTRUCTIONS as MARKETING_PLAN_INSTRUCTIONS
from src.config import get_settings

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
    "- Work IQ MCP tool が利用できない場合は、推測で続行せず失敗として扱ってください。"
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
        "Work IQ MCP 利用ガイド:\n"
        f"- 選択された職場ソース（{selected_sources}）に関係する追加文脈を確認するため、Work IQ MCP tool を優先利用してください。\n"
        "- まず Work IQ から、過去の会議・メール・チャット・社内文書にある方針、制約、過去施策、承認条件を高レベルに把握してください。\n"
        "- 原文の長い引用や個人情報の転載は避け、企画判断に必要な要点だけを要約して利用してください。\n"
        "- この実行では Work IQ MCP tool を少なくとも一度は参照してから企画書を作成してください。\n"
        "- Work IQ MCP tool が使えない場合は、推測で続行せずエラーとして終了してください。"
    )


def _build_marketing_plan_responses_web_search_tool() -> dict[str, object]:
    """Responses API で使う Web Search tool 定義を返す。"""
    return {
        "type": "web_search",
        "user_location": {"type": "approximate", "country": "JP", "region": "Tokyo"},
        "search_context_size": "medium",
    }


def _normalize_mcp_authorization_value(access_token: str) -> str:
    """MCP authorization に渡す値を Bearer 形式へ正規化する。"""
    token = access_token.strip()
    if token.lower().startswith("bearer "):
        return token
    return f"Bearer {token}"


def _build_work_iq_responses_tool(
    server_url: str,
    access_token: str,
    *,
    connection_name: str,
) -> dict[str, object]:
    """Responses API で Work IQ MCP を呼ぶための tool 定義を返す。"""
    return {
        "type": "mcp",
        "server_label": _WORK_IQ_SERVER_LABEL,
        "server_url": server_url,
        "project_connection_id": connection_name,
        "authorization": _normalize_mcp_authorization_value(access_token),
        "require_approval": "never",
        "server_description": _WORK_IQ_SERVER_DESCRIPTION,
    }


def _build_work_iq_tool_choice() -> dict[str, str]:
    """Responses API に Work IQ MCP を最低 1 回使わせる tool_choice を返す。"""
    return {"type": "mcp", "server_label": _WORK_IQ_SERVER_LABEL}


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

    model_name = settings["model_name"]
    if model_settings and isinstance(model_settings.get("model"), str) and model_settings["model"].strip():
        model_name = model_settings["model"].strip()

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
            if _resolve_work_iq_connection(project_client) is None:
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
                    "tool_choice": "required",
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
        return openai_client.responses.create(
            **response_kwargs,
        )
    finally:
        close_openai = getattr(openai_client, "close", None)
        if callable(close_openai):
            close_openai()
        close_project = getattr(project_client, "close", None)
        if callable(close_project):
            close_project()
