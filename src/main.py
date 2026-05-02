"""FastAPI エントリポイント。ルーター統合・CORS・レート制限・静的ファイル配信を行う。"""

import logging
import os
import sys
import time  # noqa: E402  - re-imported below after logging bootstrap
import uuid  # noqa: E402  - re-imported below after logging bootstrap
from collections.abc import AsyncGenerator  # noqa: E402  - re-imported below after logging bootstrap
from contextlib import asynccontextmanager  # noqa: E402  - re-imported below after logging bootstrap


def _ensure_stdout_logging() -> None:
    """stdout に StreamHandler を attach し、video_gen 等のアプリログを Container ログ・App Insights に流す。

    azure-monitor-opentelemetry は logging handler を後から attach するが、stdout への
    StreamHandler は誰もインストールしない。`logging.basicConfig` を `force=False` で呼ぶことで、
    uvicorn 等の既存ハンドラーが既に登録されている環境では no-op、未設定環境では INFO 以上を
    stdout に流すようにする。ログレベルは LOG_LEVEL 環境変数で上書きできる。
    """
    level_name = os.environ.get("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=level,
            stream=sys.stdout,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    elif root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
    for module_name in ("src", "src.agents", "src.agents.video_gen", "src.api", "src.api.chat"):
        module_logger = logging.getLogger(module_name)
        if module_logger.level == logging.NOTSET or module_logger.level > level:
            module_logger.setLevel(level)


_ensure_stdout_logging()

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.exceptions import RequestValidationError  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from src.api.capabilities import router as capabilities_router  # noqa: E402
from src.api.chat import limiter  # noqa: E402
from src.api.chat import router as chat_router  # noqa: E402
from src.api.conversations import router as conversations_router  # noqa: E402
from src.api.evaluate import router as evaluate_router  # noqa: E402
from src.api.health import router as health_router  # noqa: E402
from src.api.sources import router as sources_router  # noqa: E402
from src.api.voice import router as voice_router  # noqa: E402
from src.config import get_settings  # noqa: E402
from src.foundry_tracing import get_app_insights_association_status  # noqa: E402

logger = logging.getLogger(__name__)


def _configure_observability() -> None:
    """Application Insights の OpenTelemetry トレーシングを設定する"""
    settings = get_settings()
    conn_str = settings["applicationinsights_connection_string"]
    if not conn_str:
        logger.info("APPLICATIONINSIGHTS_CONNECTION_STRING 未設定: Observability スキップ")
        return
    association = get_app_insights_association_status(settings)
    if not association["associated"]:
        logger.warning("Application Insights connection string に関連付け ID がないため Observability スキップ")
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

    # エージェントクライアントのプリウォーム（初回レイテンシ削減）
    try:
        from src.agent_client import get_responses_client, get_shared_credential

        get_shared_credential()  # DefaultAzureCredential の初期化
        settings = get_settings()
        if settings["project_endpoint"]:
            get_responses_client()  # デフォルトモデルのクライアントを事前作成
            logger.info("エージェントクライアント プリウォーム完了")
    except (ImportError, ValueError, OSError) as exc:
        logger.info("エージェントクライアント プリウォーム スキップ: %s", exc)

    yield
    # httpx クライアントのクリーンアップ
    try:
        from src.http_client import close_http_client

        await close_http_client()
    except (ImportError, RuntimeError):
        pass


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

# API キー認証（API_KEY 環境変数が設定されている場合のみ有効化）
_API_KEY = os.environ.get("API_KEY", "")
_AUTH_EXEMPT_PATHS = {"/api/health", "/api/ready", "/", "/index.html"}
_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    """API_KEY が設定されている場合、x-api-key ヘッダーで認証する。

    APIM から Container App への通信で x-api-key を付与し、
    直接アクセスを拒否する。API_KEY 未設定時はスキップ（開発環境）。
    """
    if _API_KEY and request.url.path not in _AUTH_EXEMPT_PATHS:
        # 静的ファイルは認証不要
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        provided_key = request.headers.get("x-api-key", "")
        if provided_key != _API_KEY:
            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized — invalid or missing API key"},
            )
    return await call_next(request)


@app.middleware("http")
async def session_cookie_middleware(request: Request, call_next):
    """API リクエストに per-session cookie を attach する。

    匿名 owner_id を fingerprint (`anon-{sha256(IP+UA)}`) ではなく
    cookie session_id (`anon-{sha256(session_id)}`) ベースに移行。
    fingerprint shift で APPROVAL_CONTEXT_NOT_FOUND が起きる問題を構造的に解消。

    詳細は src/session_cookie.py の docstring を参照。

    request.state.tm_session_id に session_id を入れて、後続の
    `extract_request_identity` から参照可能にする。
    """
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    from src.session_cookie import attach_session_cookie, get_or_create_session_id

    session_id, is_new = get_or_create_session_id(request)
    request.state.tm_session_id = session_id

    response = await call_next(request)

    if is_new:
        # HTTPS のときだけ Secure cookie に。Container Apps Ingress 越しでも
        # uvicorn の --proxy-headers + --forwarded-allow-ips * で
        # x-forwarded-proto を信頼するため、request.url.scheme は正しく
        # 'https' になる (rubber-duck audit cookie-impl-review)。
        # dev 環境 (HTTP localhost) では Secure なしで動く。
        secure = request.url.scheme == "https"
        attach_session_cookie(response, session_id, secure=secure)

    return response


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
app.include_router(capabilities_router)
app.include_router(chat_router)
app.include_router(conversations_router)
app.include_router(sources_router)
app.include_router(voice_router)
app.include_router(evaluate_router)


@app.get("/auth-redirect.html", include_in_schema=False)
async def auth_redirect_bridge() -> FileResponse:
    """MSAL redirect bridge 専用ページを no-store で返す。"""
    redirect_path = os.path.join(_STATIC_DIR, "auth-redirect.html")
    if not os.path.isfile(redirect_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(
        redirect_path,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store"},
    )

# 静的ファイル配信（本番: Docker マルチステージビルドで frontend/dist を配信）
if os.environ.get("SERVE_STATIC", "").lower() == "true":
    if os.path.isdir(_STATIC_DIR):
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
