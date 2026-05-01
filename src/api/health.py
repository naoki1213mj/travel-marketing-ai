"""ヘルスチェックエンドポイント"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.config import get_missing_required_settings
from src.diagnostics import run_all_probes

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """ライブネスプローブ用ヘルスチェック (cheap、固定 200 を返す)。

    Container Apps の liveness probe / Docker HEALTHCHECK が呼ぶため、
    transient な backend 障害で pod 再起動を trigger しないよう外部 probe は行わない。
    実 dependency の認可確認は `/api/ready/deep` を別途叩く。
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready() -> JSONResponse:
    """必須設定の有無を返す readiness チェック (shallow、env var 検査のみ)。"""
    missing = get_missing_required_settings()
    if missing:
        return JSONResponse(status_code=503, content={"status": "degraded", "missing": missing})
    return JSONResponse(status_code=200, content={"status": "ready", "missing": []})


@router.get("/ready/deep")
async def ready_deep() -> JSONResponse:
    """実 dependency への認可済み呼び出しを 1 周ずつ実行する deep readiness。

    ACA / Docker の probe には繋がない (transient 障害で pod 再起動しないため)。
    Ops dashboard / nightly synthetic smoke / blue-green cutover 検証で利用する。
    今日 (2026-05-01) 発生した「Fabric Data Agent 401 → CSV silent fallback」
    のような bespoke permission 抜けはここで catch する設計。
    """
    result = await run_all_probes()
    status_code = 200 if result["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=result)

