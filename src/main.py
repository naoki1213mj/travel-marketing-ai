"""FastAPI エントリポイント。ルーター統合・CORS・静的ファイル配信を行う。"""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api.chat import router as chat_router
from src.api.health import router as health_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """アプリケーション起動・終了時のライフサイクル管理"""
    # 起動処理（将来的に Azure クライアント初期化等を追加）
    yield
    # 終了処理


app = FastAPI(
    title="Travel Marketing AI Pipeline",
    description="旅行マーケティング AI マルチエージェントパイプライン",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 設定（開発時のみ Vite dev server を許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(health_router)
app.include_router(chat_router)

# 静的ファイル配信（本番: Docker マルチステージビルドで frontend/dist を配信）
if os.environ.get("SERVE_STATIC", "").lower() == "true":
    static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
