# Travel Marketing AI Multi-Agent Pipeline

[日本語版 README はこちら](README.ja.md)

> Team D Hackathon — Auto-generate marketing plans, brochures, banner images, and promotional videos from natural language instructions

## Overview

A multi-agent pipeline where travel company marketing staff give natural language instructions and 4 AI agents sequentially produce **marketing plans (Markdown), promotional brochures (HTML), banner images (PNG), and promotional videos (MP4)**.

## アーキテクチャ

```
ユーザー → React (Vite/Tailwind/i18n) → FastAPI (SSE)
  → APIM AI Gateway → Content Safety (Prompt Shield)
  → Foundry Agent Service Workflows (Sequential + HiTL)
    → Agent1 (データ検索: Fabric Lakehouse)
    → Agent2 (施策生成: Web Search)
    → [承認ステップ]
    → Agent3 (規制チェック: Foundry IQ + Web Search)
    → Agent4 (販促物生成: GPT Image 1.5 + MCP)
  → Content Safety (Text Analysis) → 成果物表示
```

## 技術スタック

| 層 | 技術 |
|---|------|
| フロントエンド | React 19 + TypeScript + Vite 8 + Tailwind CSS 4 |
| バックエンド | FastAPI + uvicorn (Python 3.14) |
| 推論モデル | gpt-5.4-mini (GA) |
| 画像生成 | GPT Image 1.5 (GA) |
| エージェント | Microsoft Agent Framework 1.0.0rc5 |
| オーケストレーション | Foundry Agent Service Workflows (Preview) |
| データ | Fabric Lakehouse (Delta Parquet + SQL EP) |
| ナレッジ | Foundry IQ Knowledge Base (Preview) |
| デプロイ | Azure Container Apps + Docker + azd |
| CI/CD | GitHub Actions (DevSecOps) |

## Quick Start

### 前提条件

- Python 3.14+
- Node.js 22+
- [uv](https://docs.astral.sh/uv/) (Python パッケージ管理)

### セットアップ

```bash
# Python 依存インストール
uv sync

# フロントエンド依存インストール
cd frontend && npm ci && cd ..

# 環境変数設定
cp .env.example .env
# .env を編集して Azure リソースの情報を設定
```

### ローカル開発

```bash
# バックエンド起動
uv run uvicorn src.main:app --reload --port 8000

# フロントエンド起動（別ターミナル）
cd frontend && npm run dev
# → http://localhost:5173 でアクセス（/api は :8000 に proxy）
```

### テスト

```bash
uv run python -m pytest tests/ -v   # バックエンドテスト
uv run ruff check src/               # Python リント
cd frontend && npx tsc --noEmit      # TypeScript 型チェック
```

### デモデータ生成

```bash
uv run python data/demo_data_generator.py
# → data/sales_history.csv (800件), customer_reviews.csv (400件), plan_master.csv (20件)
```

### Azure デプロイ

```bash
azd auth login
azd up       # 初回: プロビジョニング + デプロイ
azd deploy   # 2回目以降: コードのみ
```

## プロジェクト構成

```
├── src/                    # バックエンド (Python)
│   ├── main.py             # FastAPI エントリポイント
│   ├── config.py           # 環境変数設定
│   ├── api/
│   │   ├── health.py       # GET /api/health
│   │   └── chat.py         # POST /api/chat (SSE)
│   ├── agents/             # 4 エージェント定義
│   ├── workflows/          # Sequential Workflow
│   └── middleware/         # Content Safety
├── frontend/               # フロントエンド (React)
│   └── src/
│       ├── components/     # 16 コンポーネント
│       ├── hooks/          # useSSE, useTheme, useI18n
│       └── lib/            # SSE client, i18n
├── data/                   # デモデータ
├── regulations/            # レギュレーション文書
├── infra/                  # Bicep IaC
├── tests/                  # pytest テスト
├── Dockerfile              # マルチステージビルド
├── azure.yaml              # azd 設定
└── .github/workflows/      # CI/CD (ci, deploy, security)
```

## チーム

| 担当 | 範囲 |
|------|------|
| Tokunaga | Fabric Lakehouse / デモデータ / Agent1 |
| Matsumoto | Frontend / Backend / Agent2 / Agent4 |
| mmatsuzaki | Infra / APIM / MCP / Agent3 / Content Safety |
