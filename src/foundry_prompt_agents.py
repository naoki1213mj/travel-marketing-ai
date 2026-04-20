"""Foundry Prompt Agent 実行ラッパー。"""

import logging
import re
from typing import TypedDict

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, MCPToolFilter, PromptAgentDefinition, WebSearchTool
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from src.agents.marketing_plan import INSTRUCTIONS as MARKETING_PLAN_INSTRUCTIONS
from src.config import get_settings

logger = logging.getLogger(__name__)

_AGENT_NAME_SANITIZER = re.compile(r"[^a-z0-9-]+")
_CONNECTOR_SPECS = {
    "meeting_notes": [
        ("connector_microsoftteams", "workiq-teams"),
        ("connector_outlookcalendar", "workiq-calendar"),
    ],
    "emails": [("connector_outlookemail", "workiq-email")],
    "teams_chats": [("connector_microsoftteams", "workiq-teams")],
    "documents_notes": [("connector_sharepoint", "workiq-sharepoint")],
}


class WorkIQPromptConfig(TypedDict):
    """Prompt Agent に渡す Work IQ tool 構成。"""

    enabled: bool
    source_scope: list[str]


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


def _ensure_marketing_plan_agent(project_client: AIProjectClient, model_name: str):
    """marketing-plan 用 Prompt Agent を取得または作成する。"""
    agent_name = _resolve_marketing_plan_agent_name(model_name)
    try:
        return project_client.agents.get(agent_name=agent_name)
    except ResourceNotFoundError:
        logger.info("marketing-plan Prompt Agent を作成します: %s", agent_name)

    return project_client.agents.create_version(
        agent_name=agent_name,
        definition=PromptAgentDefinition(
            model=model_name,
            instructions=MARKETING_PLAN_INSTRUCTIONS,
            tools=[_build_marketing_plan_web_search_tool()],
        ),
    )


def _build_work_iq_tools(config: WorkIQPromptConfig, access_token: str) -> list[MCPTool]:
    """Work IQ source_scope から read-only MCP connector 群を組み立てる。"""
    if not config["enabled"] or not access_token.strip():
        return []

    seen_connectors: set[str] = set()
    tools: list[MCPTool] = []
    for scope in config["source_scope"]:
        for connector_id, server_label in _CONNECTOR_SPECS.get(scope, []):
            if connector_id in seen_connectors:
                continue
            seen_connectors.add(connector_id)
            tools.append(
                MCPTool(
                    connector_id=connector_id,
                    server_label=server_label,
                    authorization=access_token,
                    allowed_tools=MCPToolFilter(read_only=True),
                    require_approval="never",
                )
            )
    return tools


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
    openai_client = project_client.get_openai_client()
    try:
        work_iq_tools = _build_work_iq_tools(
            work_iq or {"enabled": False, "source_scope": []},
            work_iq_access_token,
        )
        if work_iq_tools:
            response_kwargs: dict[str, object] = {
                "model": model_name,
                "instructions": MARKETING_PLAN_INSTRUCTIONS,
                "input": user_input,
                "tools": [_build_marketing_plan_web_search_tool(), *work_iq_tools],
            }
        else:
            agent = _ensure_marketing_plan_agent(project_client, model_name)
            response_kwargs = {
                "input": user_input,
                "extra_body": {"agent_reference": {"name": agent.name, "type": "agent_reference"}},
            }
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
