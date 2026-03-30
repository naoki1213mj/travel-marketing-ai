"""ヘルスチェックエンドポイント"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config import get_missing_required_settings, get_model_endpoint, get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """ライブネスプローブ用ヘルスチェック"""
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """必須設定の有無を返す readiness チェック。"""
    missing = get_missing_required_settings()
    settings = get_settings()
    if missing:
        return JSONResponse(status_code=503, content={"status": "degraded", "missing": missing})
    return JSONResponse(
        status_code=200,
        content={
            "status": "ready",
            "missing": [],
            "model_endpoint": get_model_endpoint()[:60] + "..." if get_model_endpoint() else "",
            "apim_configured": bool(settings.get("apim_gateway_url")),
        },
    )
