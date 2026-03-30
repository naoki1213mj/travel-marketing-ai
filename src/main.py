"""FastAPI エントリポイント。ルーター統合・CORS・レート制限・静的ファイル配信を行う。"""

import logging
import os
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.chat import limiter
from src.api.chat import router as chat_router
from src.api.conversations import router as conversations_router
from src.api.health import router as health_router
from src.config import get_settings

logger = logging.getLogger(__name__)


def _configure_observability() -> None:
    """Application Insights の OpenTelemetry トレーシングを設定する"""
    conn_str = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
    if not conn_str:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING 未設定: Observability スキップ")
        return
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor

        configure_azure_monitor(connection_string=conn_str)
        logger.info("Application Insights Observability 有効化")
    except ImportError:
        logger.warning("azure-monitor-opentelemetry 未インストール: Observability スキップ")


def _get_allowed_origins() -> list[str]:
    """環境変数からカンマ区切りの許可オリジンリストを返す"""
    settings = get_settings()
    raw = settings["allowed_origins"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """アプリケーション起動・終了時のライフサイクル管理"""
    _configure_observability()
    yield


app = FastAPI(
    title="Travel Marketing AI Pipeline",
    description="旅行マーケティング AI マルチエージェントパイプライン",
    version="0.1.0",
    lifespan=lifespan,
)

# レート制限
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# --- 例外ハンドラ ---


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """HTTP 例外を統一 JSON フォーマットで返す"""
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """バリデーションエラーを統一 JSON フォーマットで返す"""
    return JSONResponse(status_code=422, content={"error": "入力値が不正です", "details": str(exc)})


# --- ミドルウェア ---


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """リクエストログとリクエスト相関 ID を付与するミドルウェア"""
    request_id = request.headers.get("x-request-id", str(uuid.uuid4()))
    start_time = time.time()
    response = await call_next(request)
    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    response.headers["X-Request-Id"] = request_id
    return response


# CORS 設定（ALLOWED_ORIGINS 環境変数で制御。デフォルトは localhost:5173）
app.add_middleware(
    CORSMiddleware,
    allow_origins=_get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ルーター登録
app.include_router(health_router)
app.include_router(chat_router)
app.include_router(conversations_router)

# 静的ファイル配信（本番: Docker マルチステージビルドで frontend/dist を配信）
if os.environ.get("SERVE_STATIC", "").lower() == "true":
    static_dir = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
