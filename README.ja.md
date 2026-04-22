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
| **動画生成** | Photo Avatar + SSML ナレーション、MP4/H.264 出力 |
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

`scripts/postprovision.py` が APIM AI Gateway、MCP Function App、Voice Agent、Entra SPA 登録を自動構成します。現在の tenant 状態と残るフォローアップ項目は [docs/azure-setup.md](docs/azure-setup.md) を参照してください。

### 現在の Azure 状態（`workiq-dev` tenant）

| 領域 | 現在の状態 |
| --- | --- |
| Work IQ delegated auth | SPA redirect URI、Microsoft Graph delegated permissions、tenant-wide admin consent、Microsoft 365 Copilot ライセンス確認まで完了 |
| Work IQ runtime | 既定値は `WORKIQ_RUNTIME=graph_prefetch` です。Agent1 と Agent2 の間で Microsoft Graph Copilot Chat API（`chatOverStream` 優先、必要時 `/chat`、既定 `WORK_IQ_TIMEOUT_SECONDS=120`）から短い workplace brief を先読みするため、Foundry connector が不安定でもパイプラインが進みやすくなります。`foundry_tool` は引き続き opt-in 経路として残しており、`MARKETING_PLAN_RUNTIME=foundry_prompt` と組み合わせると Agent2 が `source_scope` に応じて read-only の Microsoft 365 connector を動的注入します。フロントエンドの auth preflight は `auth_required` / `consent_required` / `redirecting` を出し分け、バックエンドは `work_iq_session` の status を永続化するため復元後の UI 状態も一致します。tenant member / guest ではないアカウントはサインイン時に弾かれます |
| Search / Foundry IQ | `regulations-index`、`regulations-ks`、`regulations-kb` を **East US** の Azure AI Search に作成済み。アプリには `SEARCH_ENDPOINT` + `SEARCH_API_KEY` で配線済み |
| モデル配備 | メインの East US 2 Foundry account には `gpt-5-4-mini`、`gpt-4-1-mini`、`gpt-4.1`、`gpt-5.4`、`gpt-image-1.5` を配備済み。`gpt-image-2` を custom deployment 名で追加した場合は `GPT_IMAGE_2_DEPLOYMENT_NAME` で上書きできます |
| MAI 画像経路 | `IMAGE_PROJECT_ENDPOINT_MAI` は別の East US AI Services account を指す。現在は subscription に `MAI-Image-2` quota が無いため、`MAI-Image-2` deployment 名を **MAI-Image-2e** の alias として運用 |
| Fabric | Fabric capacity `fcdemojapaneast001`、workspace `ws-MG-pod2`、lakehouse `Travel_Lakehouse`、`sales_results` / `customer_reviews` テーブルは復旧済みです。live アプリには `FABRIC_DATA_AGENT_URL` と `FABRIC_SQL_ENDPOINT` の両方を反映しています |
| Logic Apps / Teams | `logic-manager-approval-wmbvhdhcsuyb2` と `logic-wmbvhdhcsuyb2` は live です。上司承認通知と承認後 Teams channel 通知を再確認済みで、`deploy.yml` は full signed manager trigger URL を Container App secret へ毎回再同期します |
| 残る手動作業 | 現状の残件は SharePoint 保存経路が中心です。Fabric、manager approval、Teams 通知は live で復旧・確認済みです |

## 環境変数

| 変数名 | 必須 | 用途 |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | 本番 | Microsoft Foundry project endpoint |
| `MODEL_NAME` | 任意 | テキスト deployment 名（既定: `gpt-5-4-mini`） |
| `EVAL_MODEL_DEPLOYMENT` | 推奨 | `/api/evaluate` 用の専用 deployment |
| `COSMOS_DB_ENDPOINT` | 任意 | 会話履歴保存（未設定時はインメモリ） |
| `SEARCH_ENDPOINT` | 任意 | Foundry IQ / 直接 KB 検索で使う Azure AI Search endpoint |
| `SEARCH_API_KEY` | 任意 | Azure AI Search 管理キー（live tenant では Container Apps secret で保持） |
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent Published URL |
| `MARKETING_PLAN_RUNTIME` | 任意 | marketing-plan の runtime 切替（既定: `foundry_prompt`、`legacy` は rollback / 検証用） |
| `WORKIQ_RUNTIME` | 任意 | Work IQ runtime 切替（既定: `graph_prefetch`）。`foundry_tool` は opt-in で、`MARKETING_PLAN_RUNTIME=foundry_prompt` が前提 |
| `WORK_IQ_TIMEOUT_SECONDS` | 任意 | `graph_prefetch` rollback 経路で Microsoft Graph Copilot Chat API から短い Work IQ brief を取得するときの timeout（既定: `120`） |
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
| AI モデル | gpt-5.4-mini · gpt-4.1 系 · gpt-5.4 · GPT Image 1.5 · MAI 経路（別 East US account） |
| エージェント | Microsoft Agent Framework 1.0.0 (GA) |
| データ | Fabric Lakehouse · Fabric Data Agent · Delta Parquet + SQL |
| ナレッジ | Foundry IQ · Azure AI Search |
| 動画 | Speech / Photo Avatar |
| 評価 | Foundry Evaluations (built-in + custom) |
| インフラ | Container Apps · APIM · Cosmos DB · Key Vault · VNet |
| CI/CD | GitHub Actions · azd · Bicep |

## ライセンス

このプロジェクトはデモンストレーション目的です。
