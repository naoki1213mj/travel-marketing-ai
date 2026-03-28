# 旅行マーケティング AI マルチエージェントパイプライン

> Team D ハッカソン — 自然言語指示から企画書・販促物・バナー画像・紹介動画を全自動生成

## 概要

旅行会社のマーケ担当者が自然言語で指示すると、4 つの AI エージェントが順次処理し、**企画書 (Markdown)・販促ブローシャ (HTML)・バナー画像 (PNG)・紹介動画 (MP4)** を全自動で生成するパイプライン。

## アーキテクチャ

```
ユーザー → React (Vite/Tailwind/i18n) + 🎤 Voice Live → FastAPI (SSE)
  → APIM AI Gateway → Content Safety (Prompt Shield)
  → Foundry Agent Service Workflows (Sequential + HiTL)
    → Agent1 (データ検索: Fabric Lakehouse + Code Interpreter)
    → Agent2 (施策生成: Web Search)
    → [承認ステップ]
    → Agent3 (規制チェック: Foundry IQ + Web Search)
    → Agent4 (販促物生成: GPT Image 1.5 + Content Understanding + Photo Avatar)
  → Content Safety (Text Analysis) → 成果物表示
  → Logic Apps (Teams 通知 + SharePoint 保存)
  → Foundry Evaluations (品質ダッシュボード)
  → Teams 公開
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
| AI Gateway | Azure API Management (BasicV2) |
| MCP サーバー | Azure Functions (Flex Consumption, Python 3.12) |
| 音声入力 | Voice Live API (Preview) |
| 文書解析 | Content Understanding (GA) |
| 販促動画 | Photo Avatar + Voice Live (Preview) |
| ワークフロー自動化 | Azure Logic Apps (Consumption) |
| デプロイ | Azure Container Apps + ACR リモートビルド + azd |
| CI/CD | GitHub Actions (DevSecOps) |

## クイックスタート

### 前提条件

- Python 3.14+
- Node.js 22+
- [uv](https://docs.astral.sh/uv/) (Python パッケージ管理)
- Azure サブスクリプション
- Azure Developer CLI (`azd`)

### セットアップ

```bash
# リポジトリクローン
git clone https://github.com/naoki1213mj/hackathon-teamD.git
cd hackathon-teamD

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
uv run pytest                         # バックエンドテスト
uv run pytest --cov=src               # カバレッジ付き
uv run ruff check src/                # Python リント
cd frontend && npx tsc --noEmit       # TypeScript 型チェック
```

### Azure デプロイ

```bash
azd auth login
azd up       # 初回: プロビジョニング + デプロイ
azd deploy   # 2回目以降: コードのみ
```

> **注**: Docker Desktop は不要です。`azd up` は ACR リモートビルド (`az acr build`) を使用します。

## プロジェクト構成

```
├── src/                    # バックエンド (Python 3.14)
│   ├── main.py             # FastAPI エントリポイント
│   ├── config.py           # 環境変数設定
│   ├── api/
│   │   ├── health.py       # GET /api/health
│   │   └── chat.py         # POST /api/chat (SSE)
│   ├── agents/             # 4 エージェント定義
│   │   ├── data_search.py  # Agent1: データ検索
│   │   ├── marketing_plan.py # Agent2: 施策生成
│   │   ├── regulation_check.py # Agent3: 規制チェック
│   │   └── brochure_gen.py # Agent4: 販促物生成
│   ├── workflows/          # Sequential Workflow
│   └── middleware/         # Content Safety
├── frontend/               # フロントエンド (React 19)
│   └── src/
│       ├── components/     # 16+ コンポーネント
│       ├── hooks/          # useSSE, useTheme, useI18n
│       └── lib/            # SSE client, i18n, export
├── functions/              # Azure Functions MCP サーバー (Python 3.12)
├── infra/                  # Bicep IaC
│   ├── main.bicep          # オーケストレーション
│   └── modules/            # 12 モジュール
├── tests/                  # pytest テスト
├── data/                   # デモデータ
├── docs/                   # ドキュメント
├── Dockerfile              # マルチステージビルド
├── azure.yaml              # azd 設定
└── .github/workflows/      # CI/CD (ci, deploy, security)
```

## チーム

| 担当 | ロール | 範囲 |
|------|--------|------|
| Tokunaga | Data SE | Fabric Lakehouse / デモデータ / Agent1 / Content Understanding |
| Matsumoto | App SE | Frontend / Backend / Agent2 / Agent4 / 販促動画 |
| mmatsuzaki | Infra SE | IaC / APIM / MCP / Agent3 / Content Safety / Observability / Voice Live / Logic Apps / Teams 公開 |

## ライセンス

このプロジェクトはハッカソン作品です。
