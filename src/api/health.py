"""ヘルスチェックエンドポイント"""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """ライブネスプローブ用ヘルスチェック"""
    return {"status": "ok"}
