# Travel Marketing AI — エージェント詳細仕様

## プロジェクト概要

旅行会社のマーケ担当者が自然言語で指示すると、企画書・販促ブローシャ・バナー画像・動画を全自動生成するマルチエージェントパイプライン。Microsoft Foundry + Azure のフル PaaS 構成。

- **チーム**: Team D (Tokunaga / Matsumoto / mmatsuzaki)
- **デプロイ先**: East US 2
- 要件定義書: [docs/requirements_v4.0.md](docs/requirements_v4.0.md)

## アーキテクチャ

```text
ユーザー → React (Vite/Tailwind/i18n) + 🎤 Voice Live → FastAPI (SSE)
  → FastAPI 直接オーケストレーション
    → Agent1 (データ検索: Fabric Lakehouse + Code Interpreter)
    → Agent2 (施策生成: Web Search)
    → [承認ステップ]
    → Agent3a (規制チェック: Foundry IQ + Web Search)
    → Agent3b (企画書修正: チェック結果を反映)
    → Agent4 (販促物生成: GPT Image 1.5 / MAI-Image-2 + Content Understanding)
    → Agent5 (動画生成: Photo Avatar)
  → モデル配備側のガードレール + 軽量ローカル入力/ツール応答ガード → 成果物表示
  → Agent6 (品質レビュー: GitHubCopilotAgent) ← オプショナル
  → Logic Apps (Teams 通知 + SharePoint 保存)
  → Foundry Evaluations (品質ダッシュボード)

補足: APIM AI Gateway は Azure 側で接続・ポリシーを構成しているが、アプリコードは project endpoint を直接使用する。
```

## 技術スタック

| 層 | 技術 | バージョン |
|---|------|----------|
| フロントエンド | React + TypeScript + Vite + Tailwind CSS | React 19, Vite 8 |
| バックエンド | FastAPI + uvicorn | Python 3.14 |
| パッケージ管理 | uv | 最新 |
| 推論モデル | gpt-5.4-mini | GA (2026-03-17~) |
| 画像生成 | GPT Image 1.5 / MAI-Image-2 | GA（UI から選択可能） |
| エージェント実装 | Microsoft Agent Framework | 1.0.0 (GA) |
| オーケストレーション | FastAPI 直接オーケストレーション | `src/api/chat.py` で実装 |
| データ | Fabric Lakehouse | Delta Parquet + SQL EP |
| ナレッジ | Foundry IQ Knowledge Base | Preview |
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
| `FoundryChatClient(project_endpoint=..., model=..., credential=DefaultAzureCredential())` | `AzureOpenAIResponsesClient(...)` | GA で Foundry クライアントへ移行 |
| `client.as_agent(name=..., tools=..., middleware=...)` | `Agent(chat_client=...)` | `chat_client` コンストラクタは旧パターン |
| `@tool` デコレータ | `@ai_function` | `@ai_function` は削除済み |
| `await agent.run("文字列")` | `agent.run(Message(role=..., contents=[...]))` | `run()` が入力を正規化する |
| `SequentialBuilder(participants=[...]).build()` | `.participants()` fluent builder | fluent builder は削除済み |
| `AZURE_AI_PROJECT_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` | Foundry Project EP を使う |
| `await call_next()` | `call_next(context)` | middleware continuation に引数は渡さない |
| `TypedDict + load_settings()` | Pydantic Settings | 軽量 settings パターンを使う |
| `uv add agent-framework-core==1.0.0 agent-framework-foundry==1.0.0` | `uv add agent-framework --prerelease=allow` | GA 版は通常インストール。beta connector だけ `--prerelease=allow` |
| `Flex Consumption` プラン | `Consumption` プラン | 旧 Consumption はレガシー |
| `Microsoft Foundry` | `Azure AI Foundry` | 2025-11 にリネーム済み |

## エージェント詳細

### Agent1: data-search-agent（データ検索）

**ファイル**: `src/agents/data_search.py`
**役割**: Fabric Lakehouse SQL endpoint から売上データ・顧客レビューをリアルタイム検索し、ターゲット・季節・地域・予算情報を抽出する。Code Interpreter による高度なデータ分析にも対応。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `search_sales_history(query, season, region)` | 売上履歴テーブルを検索。季節・地域でフィルタリング | ✅ Fabric Lakehouse (pyodbc + Azure AD トークン認証 `SQL_COPT_SS_ACCESS_TOKEN`) | CSV (`data/sales_history.csv`) → ハードコードデータ |
| `search_customer_reviews(plan_name, min_rating)` | 顧客レビューを検索。プラン名・最低評価でフィルタリング | ✅ Fabric Lakehouse (pyodbc + Azure AD トークン認証) | CSV (`data/customer_reviews.csv`) → ハードコードデータ |
| Code Interpreter | データ分析・可視化（自動検出、`ENABLE_CODE_INTERPRETER=false` で無効化可） | ✅ Foundry Agent Service | グレースフルフォールバック（ツールなしで続行） |

**出力形式**: Markdown（ターゲット分析 / 売上トレンド / 顧客評価 / 推奨事項の 4 セクション）

**データソース優先順位**: Fabric SQL endpoint → CSV ファイル → ハードコードデータ

---

### Agent2: marketing-plan-agent（施策生成）

**ファイル**: `src/agents/marketing_plan.py`
**役割**: Agent1 の分析結果をもとにマーケティング企画書を作成する。景品表示法違反表現を回避。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `search_market_trends(query)` | 最新の旅行市場トレンド・競合情報を検索 | ✅ Foundry Agent Service (Bing grounding / Web Search) | ハードコードトレンドデータ |

**出力形式**: Markdown（タイトル / キャッチコピー 3 案 / ターゲットペルソナ / プラン概要 / 差別化ポイント / 改善ポイント / 販促チャネル / KPI の 8 セクション）

---

### Agent3a: regulation-check-agent（規制チェック）

**ファイル**: `src/agents/regulation_check.py`
**役割**: 企画書のコンプライアンスを 6 項目チェック（旅行業法 / 景品表示法 / ブランドガイドライン / NG 表現 / ナレッジベース / 安全情報）。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `search_knowledge_base(query)` | レギュレーション文書をナレッジベースから検索 | ✅ Foundry IQ → Azure AI Search | 静的レスポンス |
| `check_ng_expressions(text)` | 禁止表現（最安値・業界No.1 等）をスキャン | ローカル処理（ハードコードリスト） | — |
| `check_travel_law_compliance(document)` | 旅行業法チェックリスト 5 項目を検証 | ローカル処理（キーワード検索） | — |
| `search_safety_info(destination)` | 渡航先の安全情報（外務省警告・気象警報） | ✅ Foundry Agent Service (Bing grounding) | 静的安全データ |

**出力形式**: Markdown（チェック結果テーブル ✅/⚠️/❌ / 違反詳細 / 修正提案）

---

### Agent3b: plan-revision-agent（企画書修正）

**ファイル**: `src/agents/plan_revision.py`
**役割**: 規制チェック結果を反映した修正版企画書のみ出力する。Agent3a のチェック結果（違反指摘・修正提案）と元の企画書を受け取り、すべての指摘事項を反映した完全な修正版企画書を生成する。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| （なし） | ツールは使用しない。LLM のみで企画書を修正する | — | — |

**出力形式**: 完全な修正済み企画書（Markdown）。チェック結果テーブルは含まない。

---

### Agent4: brochure-gen-agent（販促物生成）

**ファイル**: `src/agents/brochure_gen.py`
**役割**: 規制チェック済み企画書から顧客向け成果物を生成（HTML ブローシャ / ヒーロー画像 / SNS バナー）。ブローシャは**顧客向け販促資料**であり、KPI・売上目標・社内分析・競合分析などの社内情報は含めない。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `generate_hero_image(prompt, destination, style)` | 目的地メインビジュアル画像生成（1792x1024px） | ✅ GPT Image 1.5 (Responses API) / MAI-Image-2 (MAI REST API) — UI から選択 | 1x1 透明 PNG プレースホルダー |
| `generate_banner_image(prompt, platform)` | SNS バナー画像生成（Instagram/Twitter/Facebook サイズ対応） | ✅ GPT Image 1.5 (Responses API) / MAI-Image-2 (MAI REST API) — UI から選択 | 1x1 透明 PNG プレースホルダー |

**出力形式**: 顧客向け HTML ブローシャ（Tailwind CSS / レスポンシブ / 旅行業登録番号フッター付き）+ Base64 画像 data URI

**顧客向けルール**:

- 含めるべき情報: プラン名、キャッチコピー、旅行先の魅力、日程・価格帯、含まれるサービス、予約方法
- 含めてはいけない情報: KPI、目標予約数、売上目標、前年比、セグメント分析、競合分析

---

### Agent5: video-gen-agent（動画生成）

**ファイル**: `src/agents/video_gen.py`
**役割**: Photo Avatar で販促動画を生成する。企画書のサマリーを元に、ナレーション付きの紹介動画を自動作成する。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `generate_promo_video(summary, avatar_style)` | Photo Avatar プロモ動画生成（SSML ナレーション、イントロジェスチャー、`casual-sitting` スタイル、`ja-JP-Nanami:DragonHDLatestNeural` 音声、MP4/H.264） | ✅ Speech / Photo Avatar API | スキップ |

**出力形式**: MP4 動画 URL

**構成**:

- アバター: `lisa`
- スタイル: `casual-sitting`
- 音声: `ja-JP-Nanami:DragonHDLatestNeural`
- ナレーション: SSML で間と締め文を最適化し、冒頭でジェスチャーを挿入

---

### Agent6: quality-review-agent（品質レビュー）

**ファイル**: `src/agents/quality_review.py`
**役割**: 生成された成果物の品質を 4 観点でレビュー（企画書構造 / ブローシャアクセシビリティ / テキストトーン一貫性 / 旅行業法適合）。バックグラウンドで実行され、`AZURE_AI_PROJECT_ENDPOINT` 未設定時はスキップされる。

**実装**: `GitHubCopilotAgent` を優先使用し、`PermissionHandler.approve_all` で自動権限承認を設定。利用不可時は `FoundryChatClient` にフォールバック。

| ツール名 | 説明 | Azure 接続 | フォールバック |
|---------|------|-----------|-------------|
| `review_plan_quality(plan_markdown)` | 企画書の 5 必須セクション（タイトル / キャッチコピー / ターゲット / 概要 / KPI）を検証 | ローカル処理（キーワード検索） | — |
| `review_brochure_accessibility(html_content)` | HTML アクセシビリティ 4 項目チェック（alt属性 / lang属性 / フッター / フォントサイズ） | ローカル処理 | — |

**出力形式**: Markdown（セクションごとの ✅/⚠️/❌ チェックリスト）

---

## 実行フロー（FastAPI Orchestration）

```text
Agent1 (data-search-agent)
  ↓ データ分析結果
Agent2 (marketing-plan-agent)
  ↓ 企画書 Markdown
  ↓ [承認ステップ — ユーザーが承認/修正を選択]
Agent3a (regulation-check-agent)
  ↓ チェック結果（✅/⚠️/❌）
Agent3b (plan-revision-agent)
  ↓ 修正済み企画書
Agent4 (brochure-gen-agent)
  ↓ HTML ブローシャ + 画像
Agent5 (video-gen-agent)
  ↓ MP4 動画
Agent6 (quality-review-agent) ← バックグラウンド実行（オプショナル）
```

**フレームワーク**: FastAPI バックエンドによる直接オーケストレーション
**エントリポイント**: `src/api/chat.py` → `workflow_event_generator()` / `approve()`

## Azure 接続状態サマリ

| ツール / サービス | Azure 接続 | フォールバック動作 |
|-----------------|-----------|------------------|
| Fabric Lakehouse (売上・レビュー検索) | `FABRIC_SQL_ENDPOINT` 設定時（pyodbc + Azure AD トークン認証） | CSV ファイル → ハードコードデータ |
| Web Search (市場トレンド) | `AZURE_AI_PROJECT_ENDPOINT` 設定時 | ハードコードトレンドデータ |
| Foundry IQ (ナレッジベース検索) | `AZURE_AI_PROJECT_ENDPOINT` + AI Search 設定時 | 静的レスポンス |
| Web Search (安全情報) | `AZURE_AI_PROJECT_ENDPOINT` 設定時 | 静的安全データ |
| GPT Image 1.5 (画像生成) | `AZURE_AI_PROJECT_ENDPOINT` + モデルデプロイ時 | 1x1 透明 PNG |
| MAI-Image-2 (画像生成) | `IMAGE_PROJECT_ENDPOINT_MAI` + モデルデプロイ時 | 1x1 透明 PNG |
| Speech / Photo Avatar (動画生成) | `SPEECH_SERVICE_ENDPOINT` 設定時 | スキップ |
| Cosmos DB (会話履歴) | `COSMOS_DB_ENDPOINT` 設定時 | インメモリストア |

> **注**: 全環境変数が未設定の場合でもモックデモモードで動作する。

## ディレクトリ構成

```text
travel-marketing-agents/
├── src/                          # バックエンド (Python 3.14)
│   ├── agents/                   # 7 エージェント定義
│   │   ├── data_search.py        # Agent1: データ検索（Fabric SQL + CSV フォールバック + Code Interpreter）
│   │   ├── marketing_plan.py     # Agent2: 施策生成（+ Web Search ツール）
│   │   ├── regulation_check.py   # Agent3a: 規制チェック（+ 安全情報ツール）
│   │   ├── plan_revision.py      # Agent3b: 企画書修正（規制チェック結果を反映）
│   │   ├── brochure_gen.py       # Agent4: ブローシャ + 画像生成
│   │   ├── video_gen.py          # Agent5: 動画生成（Photo Avatar）
│   │   └── quality_review.py     # Agent6: 品質レビュー（§14.8）
│   ├── api/                      # FastAPI ルーター
│   │   ├── chat.py               # /api/chat (SSE) + 会話保存
│   │   ├── conversations.py      # /api/conversations + /api/replay
│   │   ├── evaluate.py           # /api/evaluate（品質評価）
│   │   ├── health.py             # /api/health + /api/ready
│   │   └── voice.py              # /api/voice-token + /api/voice-config
│   ├── middleware/                # 軽量入力 / ツール応答ガード
│   ├── conversations.py          # Cosmos DB / インメモリ会話管理
│   ├── hosted_agent.py           # Foundry Hosted Agent エントリポイント
│   ├── config.py                 # 設定（TypedDict + load_settings）
│   └── main.py                   # FastAPI エントリポイント
├── frontend/                     # フロントエンド (React 19)
│   └── src/
│       ├── components/           # UI コンポーネント群
│       ├── hooks/                # useSSE, useTheme, useI18n
│       └── lib/                  # i18n.ts, sse-client.ts, export.ts, msal-auth.ts, voice-live.ts
├── infra/                        # Bicep IaC
│   ├── main.bicep
│   └── modules/                  # VNet, Cosmos DB, APIM 等
├── data/                         # デモデータ + demo-replay.json
├── regulations/                  # レギュレーション文書
├── tests/                        # pytest テスト群
├── docs/                         # ドキュメント
│   ├── requirements_v4.0.md      # 要件定義書
│   ├── api-reference.md          # API リファレンス
│   ├── deployment-guide.md       # デプロイガイド
│   └── azure-setup.md            # Azure セットアップガイド
├── Dockerfile                    # マルチステージ（Container Apps 用）
├── Dockerfile.agent              # Hosted Agent 用
├── azure.yaml                    # azd 設定
└── .github/workflows/            # CI + Deploy + Security
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

## Breaking Changes<https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes>

<https://learn.microsoft.com/en-us/azure/foundry/agents/overview>

- Agent Framework: <https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes>
- Foundry Agent Service: <https://learn.microsoft.com/en-us/azure/foundry/agents/overview>
