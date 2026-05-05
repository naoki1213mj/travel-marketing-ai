# Travel Marketing AI

[English README](README.md)

自然言語の指示ひとつで、旅行マーケティングの企画書・規制チェック済みコピー・顧客向けブローシャ・画像・販促動画を一気通貫で生成する AI マルチエージェントパイプラインです。

> **Microsoft Foundry** + **Agent Framework 1.0** + **FastAPI** + **React 19** で構築。

## アーキテクチャ

```mermaid
flowchart TD
    user([マーケ担当者]) --> ui[React フロントエンド]
    ui --> api[FastAPI SSE API]

    subgraph pipeline[エージェントパイプライン]
        direction TB
        a1["1 · データ検索"] --> a2["2 · 施策生成"]
        a2 --> approve{担当者承認}
        approve --> a3["3 · 規制チェック + 修正"]
        a3 --> mgr{上司承認?}
        mgr -->|off| a4["4 · ブローシャ & 画像生成"]
        mgr -->|on| portal[上司承認ページ]
        portal --> a4
        a4 --> a5["5 · 動画生成"]
    end

    api --> pipeline

    subgraph azure[Azure サービス群]
        direction LR
        foundry[Microsoft Foundry]
        fabric[Fabric Lakehouse]
        search[Azure AI Search]
        speech[Speech · Photo Avatar]
        cosmos[Cosmos DB]
        apim[APIM AI Gateway]
        funcs[Functions MCP]
    end

    a1 -.-> fabric
    a2 -.-> foundry
    a3 -.-> search
    a4 -.-> foundry
    a5 -.-> speech
    api -.-> cosmos
    api -.-> apim
    apim -.-> funcs
```

詳しい Azure リソース構成は [docs/azure-architecture.md](docs/azure-architecture.md) を参照してください。

## 主な機能

| カテゴリ | 内容 |
| --- | --- |
| **マルチエージェント** | 7 エージェントを 5 ステップに集約、承認ゲート + 任意の上司承認 |
| **AI 画像生成** | GPT Image 1.5 / GPT Image 2 / MAI-Image-2（UI から選択可） |
| **動画生成** | Lisa 固定 Photo Avatar（`casual-sitting`）+ SSML ナレーション、MP4/H.264 出力 |
| **品質評価** | Built-in 指標 + 業務カスタム指標、版比較 UI |
| **評価起点の改善** | APIM 経由の Azure Functions MCP で改善ブリーフを生成 |
| **リアルタイム配信** | SSE によるエージェント単位の進捗表示（15 分タイムアウト） |
| **会話履歴** | Cosmos DB 保存、即時復元、新しい会話ボタン |
| **音声入力** | Voice Live API (MSAL.js) + Web Speech API フォールバック |
| **多言語 UI** | 日本語・英語・中国語、ダーク/ライトモード (WCAG AA) |
| **エンタープライズ連携** | Logic Apps 承認後アクション、Teams/メール通知(任意) |
| **IaC** | Bicep + azd でワンコマンド Azure デプロイ |
| **CI/CD** | GitHub Actions — Ruff, pytest, tsc, Trivy, Gitleaks |

## クイックスタート

### 前提条件

- Python 3.14+ / Node.js 22+ / [uv](https://docs.astral.sh/uv/)
- Azure デプロイ時: Azure CLI + [azd](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)

### インストール & 起動

```bash
uv sync                                  # Python 依存
cd frontend && npm ci && cd ..            # Node 依存
cp .env.example .env                      # Azure 接続情報を設定

uv run uvicorn src.main:app --reload      # バックエンド → http://localhost:8000
cd frontend && npm run dev                # フロントエンド → http://localhost:5173
```

> `AZURE_AI_PROJECT_ENDPOINT` 未設定でも**デモモード**で動作します。

### テスト & リント

```bash
uv run pytest                             # バックエンドテスト
uv run ruff check .                       # Python リント
cd frontend && npm run lint               # フロントエンドリント
cd frontend && npx tsc --noEmit           # TypeScript 型チェック
```

### Azure デプロイ

```bash
azd auth login
azd up                                    # プロビジョニング + ビルド + デプロイ
```

`scripts/postprovision.py` が APIM AI Gateway、MCP Function App、Voice Agent、marketing-plan Prompt Agent 同期、Entra SPA 登録を自動構成します。現在の tenant 状態と残るフォローアップ項目は [docs/azure-setup.md](docs/azure-setup.md) を参照してください。

### 現在の Azure 状態（`workiq-dev` tenant）

| 領域 | 現在の状態 |
| --- | --- |
| Work IQ delegated auth | SPA redirect URI、Microsoft Graph delegated permissions、tenant-wide admin consent、Microsoft 365 Copilot ライセンス確認まで完了 |
| Work IQ runtime | 既定値は `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool` です。Agent2 は事前作成済み Foundry Prompt Agent を `agent_reference` で実行し、フロントエンドが取得した `https://ai.azure.com/user_impersonation` をバックエンドが Foundry Responses へそのまま渡して、添付済みの Work IQ MCP connection を per-user で動かします。`graph_prefetch` は明示 rollback 経路として残しており、必要時だけ Microsoft Graph Copilot Chat API（`chatOverStream` 優先、必要時 `/chat`、既定 `WORK_IQ_TIMEOUT_SECONDS=120`）から短い workplace brief を先読みします。フロントエンドの auth preflight は `auth_required` / `consent_required` / `redirecting` を出し分け、バックエンドは `work_iq_session` の status を永続化するため復元後の UI 状態も一致します。tenant member / guest ではないアカウントはサインイン時に弾かれます |
| Search / Foundry IQ | `regulations-index`、`regulations-ks`、`regulations-kb` を **East US** の Azure AI Search に作成済み。アプリには `SEARCH_ENDPOINT` + `SEARCH_API_KEY` で配線済み |
| モデル配備 | テキストモデルは `gpt-5-4-mini`、`gpt-4-1-mini`、`gpt-4.1`、`gpt-5.4` を利用します。`gpt-5.5` は East US 2 の Microsoft Foundry catalog で GA（`2026-04-24`、Responses 対応）として見え、UI / postprovision code も認識します。ただし rebuilt `workiq-dev` は現時点で `gpt-5.5` quota が 0 TPM のため、実際に選択する前に deployment を作成してください。アプリの既定画像経路は `gpt-image-2` です。GPT 系画像モデルは `AZURE_AI_PROJECT_ENDPOINT` から導出した AI Services account endpoint に対して Azure OpenAI Images API を呼び、Managed Identity 認証、上限付き retry/backoff、可視 SVG fallback を使います。既定名で配備するか、deployment 名が異なる場合は `GPT_IMAGE_2_DEPLOYMENT_NAME` で上書きしてください。`gpt-image-1.5` も引き続き利用可能です |
| MAI 画像経路 | `IMAGE_PROJECT_ENDPOINT_MAI` は別の East US AI Services account を指す。現在は subscription に `MAI-Image-2` quota が無いため、`MAI-Image-2` deployment 名を **MAI-Image-2e** の alias として運用 |
| Fabric | 現在の本番運用先は workspace `ws-3iq-demo` (capacity `fcdemoeastus2001`, East US 2, F64, Active) です。Phase 9 で導入した v2 lakehouse `lh_travel_marketing_v2` (10 Delta tables in `dbo`) と Phase 10 でチューニングした Data Agent v2 `Travel_Ontology_DA_v2` が稼働中です。Agent1 は `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2` のとき `FABRIC_DATA_AGENT_URL_V2` を最優先で使い、利用不可時は `FABRIC_SQL_ENDPOINT` + `FABRIC_LAKEHOUSE_DATABASE` + 設定済み table 名、最後に CSV fallback へ退避します。legacy `Travel_LH` / `Travel_Ontology_DA` は v1 rollback 用に残しています |
| Logic Apps / Teams | `logic-manager-approval-wmbvhdhcsuyb2` と `logic-wmbvhdhcsuyb2` は live です。上司承認通知と承認後 Teams channel 通知を再確認済みで、`deploy.yml` は full signed manager trigger URL を Container App secret へ毎回再同期します |
| Container Apps | Live URL: `https://ca-wmbvhdhcsuyb2-pn.wonderfultree-f9803f6f.eastus2.azurecontainerapps.io/`. CAE `cae-wmbvhdhcsuyb2-pn` は VNet 統合 (`snet-container-apps`)。Cosmos DB / Key Vault は private endpoint 経由 (`publicNetworkAccess: Disabled`)。cutover 前の `ca-wmbvhdhcsuyb2` / `cae-wmbvhdhcsuyb2` は 2026-05-01 に削除済み |
| 承認セキュリティ | `/api/chat/{id}/approve` は per-conversation の `approval_token` (32-byte urlsafe) で保護されています。`chat()` が Agent2 完了時に発行し `approval_request` SSE event で配布、frontend が次の approve POST で echo する仕組み。匿名 lookup で token 不在 / 不一致は `APPROVAL_CONTEXT_NOT_FOUND` で即拒否。詳細は [`docs/approval-security.md`](docs/approval-security.md) |
| live health baseline | live FQDN 上で `/api/health` (`{"status":"ok"}`) と `/api/ready` (`{"status":"ready","missing":[]}`) を確認してください。revision ID は deploy ごとに進むため、最新確認は `gh run list --workflow=deploy.yml` を正としてください |
| 残る手動作業 | 現状の残件は SharePoint 保存経路が中心です。Fabric、manager approval、Teams 通知は live で復旧・確認済みです。Phase 10 で残った P13 / P14 prompts は Fabric platform-side `submit_tool_outputs` BadRequest 問題で Microsoft サポート起票待ち |

## 環境変数

| 変数名 | 必須 | 用途 |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | 本番 | Microsoft Foundry project endpoint |
| `MODEL_NAME` | 任意 | テキスト deployment 名（既定: `gpt-5-4-mini`） |
| `EVAL_MODEL_DEPLOYMENT` | 推奨 | `/api/evaluate` 用の専用 deployment |
| `COSMOS_DB_ENDPOINT` | 任意 | 会話履歴保存（未設定時はインメモリ） |
| `SEARCH_ENDPOINT` | 任意 | Foundry IQ / 直接 KB 検索で使う Azure AI Search endpoint |
| `SEARCH_API_KEY` | 任意 | Azure AI Search 管理キー（live tenant では Container Apps secret で保持） |
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent v1 Published URL (`Travel_Ontology_DA` / `travel_sales` / `travel_review` schema、rollback 用) |
| `FABRIC_DATA_AGENT_URL_V2` | 推奨 | Fabric Data Agent v2 Published URL (`Travel_Ontology_DA_v2` / `lh_travel_marketing_v2`、現在の本番) |
| `FABRIC_DATA_AGENT_RUNTIME_VERSION` | 任意 | `v1` (既定) または `v2`。`v2` にすると Agent1 が Phase 9/10 v2 lakehouse へルーティング |
| `FABRIC_SQL_ENDPOINT` | 任意 | Fabric SQL analytics endpoint fallback |
| `FABRIC_LAKEHOUSE_DATABASE` | 任意 | Fabric SQL fallback 用 Lakehouse database 名（コード default: `Travel_Lakehouse`、live 本番値: `lh_travel_marketing_v2`） |
| `FABRIC_SALES_TABLE` | 任意 | Fabric SQL fallback 用販売テーブル名（コード default: `sales_results`、v2 lakehouse 運用時の推奨値: `booking`） |
| `FABRIC_REVIEWS_TABLE` | 任意 | Fabric SQL fallback 用レビューテーブル名（コード default: `customer_reviews`、v2 lakehouse 運用時の推奨値: `tour_review`） |
| `MARKETING_PLAN_RUNTIME` | 任意 | marketing-plan の runtime 切替（既定: `foundry_preprovisioned`、`legacy` は rollback / 検証用） |
| `WORKIQ_RUNTIME` | 任意 | Work IQ runtime 切替（既定: `foundry_tool`、`graph_prefetch` は明示 rollback 用） |
| `WORK_IQ_TIMEOUT_SECONDS` | 任意 | `graph_prefetch` rollback 経路で Microsoft Graph Copilot Chat API から短い Work IQ brief を取得するときの timeout（既定: `120`） |
| `PUBLIC_APP_BASE_URL` | 上司承認で推奨 | manager approval / callback URL を生成するときに使う公開ベース URL |
| `ENABLE_GITHUB_COPILOT_REVIEW_AGENT` | 任意 | preview の `GitHubCopilotAgent` 品質レビュー経路を opt-in するフラグ。既定 `false` では Foundry review fallback を使います |
| `SPEECH_SERVICE_ENDPOINT` | 任意 | Photo Avatar 動画生成 |
| `IMPROVEMENT_MCP_ENDPOINT` | 任意 | 評価改善用 APIM MCP ルート |
| `IMAGE_PROJECT_ENDPOINT_MAI` | 任意 | MAI 対応の別 AI Services endpoint |

全項目は [.env.example](.env.example) を参照してください。

## ディレクトリ構成

```text
src/                 FastAPI バックエンド、エージェント定義、ミドルウェア
  agents/            7 エージェント（データ検索 → 品質レビュー）
  api/               REST + SSE エンドポイント
frontend/            React 19 · Vite · Tailwind CSS · i18n
infra/               Bicep IaC モジュール
data/                デモ CSV データとリプレイペイロード
regulations/         ナレッジベース投入用の規制文書
tests/               バックエンド pytest テスト
scripts/             ポストプロビジョン & デプロイ自動化
docs/                アーキテクチャ、API リファレンス、デプロイガイド
```

## ドキュメント

| ドキュメント | 説明 |
| --- | --- |
| [docs/azure-architecture.md](docs/azure-architecture.md) | Azure リソース構成とランタイムフロー |
| [docs/api-reference.md](docs/api-reference.md) | REST API と SSE イベント仕様 |
| [docs/deployment-guide.md](docs/deployment-guide.md) | ローカル、Docker、CI/CD、Azure デプロイ |
| [docs/azure-setup.md](docs/azure-setup.md) | ポストプロビジョン設定とトラブルシューティング |
| [AGENTS.md](AGENTS.md) | エージェント詳細と技術スタック |

## 実装ステータス (2026-04-10)

| 項目 | 値 |
| --- | --- |
| バックエンド Python | 24 ファイル · 6,764 行 |
| フロントエンド React/TS | 44 ファイル · 6,764 行 (30 TSX + 14 TS) |
| インフラ (Bicep) | 16 ファイル · 1,227 行 |
| テストカバレッジ | 68% (277 pytest · 83 vitest) |
| テストコード | 32 ファイル · 6,890 行 (18 pytest + 14 vitest) |
| UI コンポーネント | 28 コンポーネント |
| SSE イベント種別 | 7 種 (agent_progress, tool_event, text, image, approval_request, error, done) |
| エージェント | 7 体 (データ検索 / 施策生成 / 規制チェック / 企画書修正 / ブローシャ生成 / 動画生成 / 品質レビュー) |
| CI/CD ワークフロー | 3 本 (CI · Deploy · Security) |

## 技術スタック

| 層 | 技術 |
| --- | --- |
| フロントエンド | React 19 · TypeScript · Vite 8 · Tailwind CSS 4 |
| バックエンド | Python 3.14 · FastAPI · uvicorn |
| AI モデル | gpt-5.4-mini · gpt-4.1 系 · gpt-5.4 · GPT Image 2（既定）· GPT Image 1.5 · MAI 経路（別 East US account） |
| エージェント | Microsoft Agent Framework 1.0.0 (GA) |
| データ | Fabric Lakehouse · Fabric Data Agent · Delta Parquet + SQL |
| ナレッジ | Foundry IQ · Azure AI Search |
| 動画 | Speech / Photo Avatar |
| 評価 | Foundry Evaluations (built-in + custom) |
| インフラ | Container Apps · APIM · Cosmos DB · Key Vault · VNet |
| CI/CD | GitHub Actions · azd · Bicep |

## ライセンス

このプロジェクトはデモンストレーション目的です。
