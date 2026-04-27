"""安全な機能可用性エンドポイント。"""

from fastapi import APIRouter

from src.capabilities import CapabilitySnapshot, build_capability_snapshot

router = APIRouter(prefix="/api", tags=["capabilities"])


@router.get("/capabilities")
async def capabilities() -> CapabilitySnapshot:
    """機密情報を含まないロードマップ機能の可用性を返す。"""
    return build_capability_snapshot()
