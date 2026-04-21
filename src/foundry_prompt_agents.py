"""Foundry Prompt Agent 実行ラッパー。"""

import logging
import re
import time
from datetime import datetime, timezone
from typing import TypedDict

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import MCPTool, MCPToolFilter, PromptAgentDefinition, WebSearchTool
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import DefaultAzureCredential

from src.agents.marketing_plan import INSTRUCTIONS as MARKETING_PLAN_INSTRUCTIONS
from src.config import get_settings
from src.tool_telemetry import build_tool_event_data, emit_tool_event

logger = logging.getLogger(__name__)

_AGENT_NAME_SANITIZER = re.compile(r"[^a-z0-9-]+")
_CONNECTOR_SPECS = {
    "meeting_notes": [
        ("connector_microsoftteams", "workiq-teams", "Work IQ Teams"),
    ],
    "emails": [("connector_outlookemail", "workiq-email", "Work IQ Mail")],
    "teams_chats": [("connector_microsoftteams", "workiq-teams", "Work IQ Teams")],
    "documents_notes": [("connector_sharepoint", "workiq-sharepoint", "Work IQ SharePoint")],
}
_SOURCE_LABELS = {
    "meeting_notes": "会議メモ",
    "emails": "メール",
    "teams_chats": "Teams チャット",
    "documents_notes": "ドキュメント / ノート",
}


class WorkIQPromptConfig(TypedDict):
    """Prompt Agent に渡す Work IQ tool 構成。"""

    enabled: bool
    source_scope: list[str]


class WorkIQResolvedTool(TypedDict):
    """Work IQ connector の安全な表示用メタデータ。"""

    connector_id: str
    server_label: str
    display_name: str
    source_scope: str


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


def _utc_now_iso() -> str:
    """UTC 現在時刻を ISO 文字列で返す。"""
    return datetime.now(timezone.utc).isoformat()


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


def _build_work_iq_tools(
    config: WorkIQPromptConfig, access_token: str
) -> tuple[list[MCPTool], list[WorkIQResolvedTool]]:
    """Work IQ source_scope から read-only MCP connector 群を組み立てる。"""
    if not config["enabled"] or not access_token.strip():
        return [], []

    seen_connectors: set[str] = set()
    tools: list[MCPTool] = []
    resolved_tools: list[WorkIQResolvedTool] = []
    for scope in config["source_scope"]:
        for connector_id, server_label, display_name in _CONNECTOR_SPECS.get(scope, []):
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
            resolved_tools.append(
                {
                    "connector_id": connector_id,
                    "server_label": server_label,
                    "display_name": display_name,
                    "source_scope": scope,
                }
            )
    return tools, resolved_tools


def _build_work_iq_tool_guidance(
    config: WorkIQPromptConfig,
    resolved_tools: list[WorkIQResolvedTool],
) -> str:
    """Work IQ connector 利用時の追加指示を構築する。"""
    source_labels = [_SOURCE_LABELS.get(scope, scope) for scope in config["source_scope"] if scope]
    selected_sources = "、".join(source_labels) if source_labels else "Microsoft 365"
    connector_names = "、".join(dict.fromkeys(tool["display_name"] for tool in resolved_tools))
    return (
        "Microsoft 365 参照ガイド:\n"
        f"- 選択された職場ソース（{selected_sources}）に関係する追加文脈が必要なときだけ、利用可能な Work IQ tools（{connector_names}）を参照してください。\n"
        "- まず Work IQ から、過去の会議・メール・チャット・社内文書にある方針、制約、過去施策、承認条件を高レベルに把握してください。\n"
        "- 原文の長い引用や個人情報の転載は避け、企画判断に必要な要点だけを要約して利用してください。\n"
        "- Work IQ tool が使えない、遅い、または十分な結果がない場合はブロックせず、利用可能な情報だけで企画を続行してください。"
    )


def _emit_work_iq_tool_event(
    status: str,
    *,
    source_scope: list[str],
    started_at: str | None = None,
    duration_ms: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Work IQ foundry_tool 実行状態を canonical tool_event として流す。"""
    emit_tool_event(
        build_tool_event_data(
            "workiq_foundry_tool",
            status,
            agent_name="marketing-plan-agent",
            step=2,
            source="workiq",
            provider="foundry",
            display_name="Work IQ context tools",
            started_at=started_at,
            finished_at=_utc_now_iso() if status != "running" else None,
            duration_ms=duration_ms,
            error_code=error_code,
            error_message=error_message,
            source_scope=source_scope,
        )
    )


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
        work_iq_tools, resolved_work_iq_tools = _build_work_iq_tools(
            work_iq or {"enabled": False, "source_scope": []},
            work_iq_access_token,
        )
        if work_iq_tools:
            started_at = _utc_now_iso()
            started_perf = time.perf_counter()
            _emit_work_iq_tool_event(
                "running",
                started_at=started_at,
                source_scope=list((work_iq or {"enabled": False, "source_scope": []})["source_scope"]),
            )
            response_kwargs: dict[str, object] = {
                "model": model_name,
                "instructions": (
                    f"{MARKETING_PLAN_INSTRUCTIONS.rstrip()}\n\n"
                    f"{_build_work_iq_tool_guidance(work_iq or {'enabled': False, 'source_scope': []}, resolved_work_iq_tools)}"
                ),
                "input": user_input,
                "tools": [_build_marketing_plan_web_search_tool(), *work_iq_tools],
            }
            try:
                response = openai_client.responses.create(
                    **response_kwargs,
                )
            except Exception as exc:
                duration_ms = max(int((time.perf_counter() - started_perf) * 1000), 0)
                _emit_work_iq_tool_event(
                    "failed",
                    started_at=started_at,
                    duration_ms=duration_ms,
                    error_code=exc.__class__.__name__,
                    error_message=str(exc)[:500],
                    source_scope=list((work_iq or {"enabled": False, "source_scope": []})["source_scope"]),
                )
                raise
            duration_ms = max(int((time.perf_counter() - started_perf) * 1000), 0)
            _emit_work_iq_tool_event(
                "completed",
                started_at=started_at,
                duration_ms=duration_ms,
                source_scope=list((work_iq or {"enabled": False, "source_scope": []})["source_scope"]),
            )
            return response
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
