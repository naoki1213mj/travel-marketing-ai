"""音声入力（Azure Speech Speech-to-Text）の設定・トークンエンドポイント。

ブラウザの音声入力は Azure AI Speech の Speech-to-Text を keyless（Managed Identity）で
利用する。FastAPI が `DefaultAzureCredential` で Cognitive Services の AAD トークンを取得し、
Speech リソースの custom-domain `issueToken` エンドポイントから短命の authorization token を
発行してブラウザへ渡す。ブラウザ側は Speech SDK の `fromAuthorizationToken` で STT する。

旧 Voice Live（リアルタイム音声会話）方式は廃止。本機能の実体は「発話を入力欄へ文字起こし
する STT」であり、音声会話エージェントや MSAL ユーザーログインは不要なため。

なお `/api/voice-config` は Work IQ の delegated 認証（`frontend/src/lib/api-auth.ts`）が
MSAL クライアント設定の取得元として利用するため、互換のため MSAL 設定を返し続ける。音声入力の
可用性は別エンドポイント `/api/speech-config` で公開する。
"""

import asyncio
import logging
import time

import httpx
from azure.core.exceptions import AzureError
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from src.api.chat import limiter
from src.config import get_settings
from src.http_client import get_http_client

router = APIRouter(prefix="/api", tags=["voice"])
logger = logging.getLogger(__name__)

# Speech STT は Cognitive Services account scope の AAD トークンで keyless 認証する。
_COGNITIVE_SERVICES_SCOPE = "https://cognitiveservices.azure.com/.default"
_ISSUE_TOKEN_PATH = "sts/v1.0/issueToken"
_ISSUE_TOKEN_TIMEOUT_SECONDS = 10.0
# Speech の authorization token は 10 分有効。9 分で更新させるため expires_at を 540 秒先に置く。
_TOKEN_TTL_SECONDS = 540
_STT_LANGUAGE = "ja-JP"


def _speech_stt_target() -> tuple[str, str]:
    """Speech STT に必要な custom-domain endpoint と region を返す。未設定時は ("", "")。"""
    settings = get_settings()
    endpoint = settings["speech_service_endpoint"].strip().rstrip("/")
    region = settings["speech_service_region"].strip()
    if not endpoint or not region:
        return "", ""
    return endpoint, region


def _token_unavailable_response() -> JSONResponse:
    """Speech STT トークンを発行できない場合の共通レスポンス。"""
    return JSONResponse(
        status_code=503,
        content={"error": "Speech STT token unavailable", "code": "SPEECH_TOKEN_UNAVAILABLE"},
    )


@router.get("/voice-token")
async def get_voice_token() -> JSONResponse:
    """廃止済み endpoint であることを返す（後方互換）。"""
    return JSONResponse(
        status_code=410,
        content={
            "error": "Voice token endpoint disabled",
            "code": "VOICE_TOKEN_ENDPOINT_DISABLED",
            "message": "Use /api/speech-token for keyless Azure Speech Speech-to-Text authorization tokens.",
        },
    )


@router.get("/voice-config")
async def get_voice_config() -> JSONResponse:
    """Work IQ の delegated 認証が使う MSAL クライアント設定を返す（機密値は含まない）。"""
    settings = get_settings()
    return JSONResponse(
        content={
            "client_id": settings["entra_client_id"].strip(),
            "tenant_id": settings["entra_tenant_id"].strip(),
        }
    )


@router.get("/speech-config")
async def get_speech_config() -> JSONResponse:
    """音声入力モードの可用性を返す（機密値を含まない）。"""
    endpoint, region = _speech_stt_target()
    available = bool(endpoint and region)
    return JSONResponse(
        content={
            "mode": "azure_speech" if available else "web_speech",
            "region": region,
            "language": _STT_LANGUAGE,
            "available": available,
        }
    )


@router.get("/speech-token")
@limiter.limit("30/minute")
async def get_speech_token(request: Request) -> JSONResponse:
    """Azure Speech STT 用の短命 authorization token を Managed Identity で発行する。

    匿名ブラウザ利用を許容するため認証は課さないが、Speech リソースの quota 濫用を防ぐため
    送信元 IP ごとにレート制限する。AAD トークン自体はブラウザへ返さず、派生した Speech の
    service token のみを返す。
    """
    endpoint, region = _speech_stt_target()
    if not endpoint or not region:
        return JSONResponse(
            status_code=503,
            content={"error": "Speech STT is not configured", "code": "SPEECH_STT_NOT_CONFIGURED"},
        )

    try:
        from src.agent_client import get_shared_credential

        aad_token = await asyncio.to_thread(get_shared_credential().get_token, _COGNITIVE_SERVICES_SCOPE)
    except AzureError:
        logger.warning("Speech STT token acquisition failed during credential resolution")
        return _token_unavailable_response()

    client = get_http_client()
    try:
        response = await client.post(
            f"{endpoint}/{_ISSUE_TOKEN_PATH}",
            headers={"Authorization": f"Bearer {aad_token.token}"},
            content=b"",
            timeout=_ISSUE_TOKEN_TIMEOUT_SECONDS,
        )
    except httpx.HTTPError:
        logger.warning("Speech STT issueToken request failed")
        return _token_unavailable_response()

    if response.status_code >= 400:
        logger.warning("Speech STT issueToken returned HTTP %s", response.status_code)
        return _token_unavailable_response()

    token = response.text.strip()
    if not token:
        logger.warning("Speech STT issueToken returned an empty token")
        return _token_unavailable_response()

    return JSONResponse(
        content={
            "token": token,
            "region": region,
            "language": _STT_LANGUAGE,
            "expires_at": int(time.time()) + _TOKEN_TTL_SECONDS,
        },
        headers={"Cache-Control": "no-store"},
    )
