# Team D ハッカソン — 旅行マーケティング AI マルチエージェント

## プロジェクト概要

旅行会社のマーケ担当者が自然言語で指示すると、企画書・販促ブローシャ・バナー画像を全自動生成するマルチエージェントパイプライン。Microsoft Foundry + Azure のフル PaaS 構成。

要件定義書: `docs/requirements_v3.md`

## ハッカソン情報

- **チーム**: Team D (Tokunaga / Matsumoto / mmatsuzaki)
- **Deadline**: 未確認（チームに確認すること）
- **審査基準**: 未確認（チームに確認すること）
- **リポジトリ**: Public（ハッカソン要件）
- **デプロイ先**: East US 2（Code Interpreter のリージョン可用性による）

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

| 層 | 技術 | バージョン |
|---|------|----------|
| フロントエンド | React + TypeScript + Vite + Tailwind CSS | React 18, Vite 6 |
| バックエンド | FastAPI + uvicorn | Python 3.14 |
| パッケージ管理 | uv | 最新 |
| 推論モデル | gpt-5.4-mini | GA (2026-03-17~) |
| 画像生成 | GPT Image 1.5 | GA（アクセス承認必要） |
| エージェント実装 | Microsoft Agent Framework | 1.0.0rc5 (RC) |
| オーケストレーション | Foundry Agent Service Workflows | Preview |
| データ | Fabric Lakehouse | Delta Parquet + SQL EP |
| ナレッジ | Foundry IQ Knowledge Base | Preview |
| MCP サーバー | Azure Functions (Flex Consumption) | Preview |
| AI Gateway | Azure API Management | GA |
| デプロイ | Azure Container Apps + azd | GA |
| CI/CD | GitHub Actions (DevSecOps) | — |
| 音声入力 | Voice Live API | Preview |
| 文書解析 | Content Understanding | GA |
| 販促動画 | Photo Avatar + Voice Live | Preview |
| ワークフロー自動化 | Azure Logic Apps | GA |
| 配信チャネル | Microsoft Teams | GA |

## 間違えやすい API / 設定

| ✅ 正しい | ❌ 間違い | 理由 |
|----------|---------|------|
| `AzureOpenAIResponsesClient(project_endpoint=..., credential=DefaultAzureCredential())` | `AzureOpenAIChatClient(endpoint=...)` | rc5 で ChatClient は廃止 |
| `client.as_agent(name=..., tools=..., middleware=...)` | `Agent(chat_client=...)` | rc5 でコンストラクタ変更 |
| `@tool` デコレータ | `@ai_function` | `@ai_function` は削除済み |
| `await agent.run("文字列")` | `agent.run(Message(role=..., contents=[...]))` | rc5 で簡素化 |
| `SequentialBuilder(participants=[...]).build()` | `.participants()` fluent builder | fluent builder は削除済み |
| `AZURE_AI_PROJECT_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` | Foundry Project EP を使う |
| `await call_next()` | `call_next(context)` | middleware の引数なし（rc5） |
| `TypedDict + load_settings()` | Pydantic Settings | rc5 で廃止 |
| `uv add agent-framework --prerelease=allow` | `pip install agent-framework` | uv でプレリリース指定が必要 |
| `Flex Consumption` プラン | `Consumption` プラン | 旧 Consumption はレガシー |
| `Microsoft Foundry` | `Azure AI Foundry` | 2025-11 にリネーム済み |

## ディレクトリ構成

```
travel-marketing-agents/
├── src/                          # バックエンド (Python)
│   ├── agents/                   # 4 つのエージェント定義
│   │   ├── data_search.py        # Agent1
│   │   ├── marketing_plan.py     # Agent2
│   │   ├── regulation_check.py   # Agent3
│   │   └── brochure_gen.py       # Agent4
│   ├── workflows/                # Foundry Workflows 定義
│   ├── tools/                    # @tool 関数
│   ├── api/                      # FastAPI ルーター
│   │   ├── chat.py               # /api/chat (SSE)
│   │   └── health.py             # /api/health
│   ├── middleware/                # Content Safety 等
│   ├── config.py                 # 設定（TypedDict + load_settings）
│   └── main.py                   # FastAPI エントリポイント
├── frontend/                     # フロントエンド (React)
│   ├── src/
│   │   ├── components/           # §6.2 の 16 コンポーネント群
│   │   ├── hooks/                # useSSE, useTheme, useI18n
│   │   ├── lib/                  # i18n.ts, sse-client.ts
│   │   └── App.tsx
│   ├── package.json
│   └── vite.config.ts
├── functions/                    # Azure Functions MCP サーバー
├── infra/                        # Bicep IaC
│   ├── main.bicep
│   └── modules/
├── tests/                        # pytest
├── docs/                         # 要件定義書等
│   └── requirements_v3.md
├── azure.yaml                    # azd 設定
├── Dockerfile                    # マルチステージビルド
├── pyproject.toml
└── .env.example
```

## Quick Commands

```bash
# 依存インストール
uv sync
cd frontend && npm ci && cd ..

# ローカル開発
uv run uvicorn src.main:app --reload --port 8000
cd frontend && npm run dev              # Vite dev server (proxy → :8000)

# テスト
uv run pytest
cd frontend && npm run test

# リント
uv run ruff check .
cd frontend && npx tsc --noEmit

# デプロイ
azd up                                   # 初回: プロビジョニング + デプロイ
azd deploy                               # 2 回目以降: コードのみ

# Docker ローカル確認
docker build -t travel-agents .
docker run -p 8000:8000 --env-file .env travel-agents
```

## 変更の規律

- 依頼された変更だけ行う。隣接コードを勝手に「改善」しない
- 既存のスタイルに合わせる
- コミット・push は、現在の会話で明示依頼がある場合だけ行う
- Azure 本番への直接反映は、理由と影響範囲を先に説明して了承を得る

## Breaking Changes 確認先

- Agent Framework: https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes
- Foundry Agent Service: https://learn.microsoft.com/en-us/azure/foundry/agents/overview
