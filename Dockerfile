# Stage 1: フロントエンドビルド
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python バックエンド + 静的ファイル配信
FROM python:3.14-slim
WORKDIR /app

# uv インストール
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python 依存関係インストール
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# アプリケーションコード
COPY src/ ./src/

# フロントエンドビルド成果物
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# 非 root ユーザーで実行（セキュリティベストプラクティス）
RUN adduser --disabled-password --no-create-home appuser
USER appuser

# 環境変数
ENV SERVE_STATIC=true
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD uv run python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
