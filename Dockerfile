# Stage 1: フロントエンドビルド
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN node node_modules/typescript/bin/tsc -b && node node_modules/vite/bin/vite.js build

# Stage 2: Python バックエンド + 静的ファイル配信
FROM python:3.14-slim
WORKDIR /app

# uv インストール
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python 依存関係インストール
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# アプリケーションコード + データ
COPY src/ ./src/
COPY data/ ./data/
COPY regulations/ ./regulations/

# フロントエンドビルド成果物
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# 非 root ユーザーで実行（セキュリティベストプラクティス）
RUN adduser --disabled-password --no-create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

# 環境変数
ENV SERVE_STATIC=true
ENV PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD [".venv/bin/python", "-m", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
