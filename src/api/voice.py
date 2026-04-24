"""Voice Live 設定エンドポイント。"""

import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["voice"])


def _get_foundry_voice_target() -> tuple[str, str, str]:
    """Voice Live 接続先に必要なリソース名・プロジェクト名・エンドポイントを返す。"""
    project_endpoint = os.environ.get("AZURE_AI_PROJECT_ENDPOINT", "")
    if not project_endpoint:
        return "", "", ""

    try:
        resource_name = project_endpoint.split("//", 1)[1].split(".", 1)[0]
        project_name = project_endpoint.rstrip("/").rsplit("/", 1)[1]
    except IndexError, AttributeError:
        return "", "", ""

    endpoint = f"wss://{resource_name}.services.ai.azure.com/voice-live/realtime"
    return resource_name, project_name, endpoint


@router.get("/voice-token")
async def get_voice_token() -> JSONResponse:
    """廃止済み endpoint であることを返す。"""
    return JSONResponse(
        status_code=410,
        content={
            "error": "Voice token endpoint disabled",
            "code": "VOICE_TOKEN_ENDPOINT_DISABLED",
            "message": (
                "Use /api/voice-config and browser delegated MSAL auth with "
                "https://cognitiveservices.azure.com/user_impersonation."
            ),
        },
    )


@router.get("/voice-config")
async def get_voice_config() -> JSONResponse:
    """Voice Live の MSAL 設定情報を返す。"""
    agent_name = os.environ.get("VOICE_AGENT_NAME", "travel-voice-orchestrator")
    client_id = os.environ.get("VOICE_SPA_CLIENT_ID", "")
    tenant_id = os.environ.get("AZURE_TENANT_ID", "")
    resource_name, project_name, endpoint = _get_foundry_voice_target()

    return JSONResponse(
        content={
            "agent_name": agent_name,
            "client_id": client_id,
            "tenant_id": tenant_id,
            "resource_name": resource_name,
            "project_name": project_name,
            "voice": "ja-JP-NanamiNeural",
            "vad_type": "azure_semantic_vad",
            "endpoint": endpoint,
            "api_version": "2026-01-01-preview",
        }
    )
