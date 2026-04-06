# デプロイガイド

このガイドは、現在のリポジトリと GitHub Actions 定義に合わせたデプロイ手順です。

## 1. 前提条件

### 開発ツール

- Python 3.14+
- Node.js 22+
- [uv](https://docs.astral.sh/uv/)
- [Azure CLI](https://learn.microsoft.com/ja-jp/cli/azure/install-azure-cli)
- [Azure Developer CLI (azd)](https://learn.microsoft.com/ja-jp/azure/developer/azure-developer-cli/install-azd)
- Git

### Azure 側の前提

- Azure サブスクリプション
- Microsoft Foundry の利用権限
- East US 2 もしくは同等の対応リージョン

注: Docker Desktop はローカル `docker build` のときだけ必要です。Azure デプロイ自体は `az acr build` のリモートビルドで進みます。

## 2. ローカル開発

### セットアップ

```bash
git clone https://github.com/naoki1213mj/travel-marketing-ai.git
cd travel-marketing-ai
uv sync
cd frontend && npm ci && cd ..
cp .env.example .env
```

最小限の Azure 接続を使う場合は、`.env` に以下を設定します。

```env
AZURE_AI_PROJECT_ENDPOINT=https://your-foundry.services.ai.azure.com/api/projects/your-project
EVAL_MODEL_DEPLOYMENT=gpt-4-1-mini
```

`AZURE_AI_PROJECT_ENDPOINT` を入れなければモック / デモモードで動作します。

Fabric の実データを自然言語で引かせたい場合は、追加で `FABRIC_DATA_AGENT_URL` に Published URL（`.../aiassistant/openai`）を設定します。未設定時は `FABRIC_SQL_ENDPOINT`、それもなければ CSV フォールバックです。

### 起動

```bash
uv run uvicorn src.main:app --reload --port 8000
cd frontend && npm run dev
```

### 動作確認

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/ready
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"沖縄のファミリー向け春キャンペーンを企画してください"}'
```

## 3. テストとビルド

```bash
uv run pytest
uv run ruff check .
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
cd frontend && npm run build
```

## 4. Docker

### ローカルビルド

```bash
docker build -t travel-agents .
docker run -p 8000:8000 --env-file .env travel-agents
```

### Dockerfile の現状

- フロントエンド stage: `npm ci --force` で依存関係を解決し、`tsc -b` と `vite build` を実行
- Python stage: `python:3.14-slim` + `uv sync --frozen --no-dev`
- ヘルスチェック: `/api/health`
- 実行ユーザー: non-root

### ACR リモートビルド

```bash
az acr build \
  --registry <your-acr-name> \
  --image travel-agents:latest \
  --file Dockerfile \
  .
```

## 5. Azure デプロイ (`azd`)

### 初回

```bash
azd auth login
azd up
```

`azd up` により、Bicep でインフラを作成し、ACR リモートビルドと Container Apps デプロイまで進みます。

### 2 回目以降

```bash
azd deploy
```

### 重要な補足

- IaC は既定のテキストモデル (`gpt-5-4-mini`) と画像モデル (`gpt-image-1.5`) を自動配備します
- `infra/modules/ai-services.bicep` では Foundry 配下の model deployment を直列化しています。これは Azure 側で同一親リソース配下の deployment 更新が並列実行されると `RequestConflict` になりやすいためです
- MAI-Image-2 を使う場合は別リソースにデプロイし、`IMAGE_PROJECT_ENDPOINT_MAI` に加えて `MAI_RESOURCE_NAME` も設定してください（Container App の Managed Identity に別リソース RBAC を付与するため）
- Container App には Content Understanding / Speech / post approval Logic Apps callback の基本設定も自動注入されます
- Container App には `IMPROVEMENT_MCP_ENDPOINT` も自動注入されます。APIM 側に `improvement-mcp` API がまだ登録されていない場合でも、アプリは従来の改善ロジックへフォールバックして動作を継続します
- `scripts/postprovision.py` は improvement MCP 用に `func-mcp-<resourceToken>` と `stfn<resourceToken>` を既定名として導出し、Function App の作成、zip 配備、APIM の `improvement-mcp` named value / backend / API / policy の同期まで自動実行します。Function App を別 RG や別名で管理したい場合だけ `IMPROVEMENT_MCP_FUNCTION_APP_NAME` / `IMPROVEMENT_MCP_FUNCTION_APP_RESOURCE_GROUP` / `IMPROVEMENT_MCP_STORAGE_ACCOUNT_NAME` を上書きしてください
- post-provision で残る主作業は Azure AI Search の接続・`regulations-index` の投入、必要に応じた `FABRIC_DATA_AGENT_URL` または `FABRIC_SQL_ENDPOINT` の設定、評価専用モデルを分ける場合の `EVAL_MODEL_DEPLOYMENT` 設定です
- 上司承認を使う場合、アプリ自体が上司承認ページ URL を発行するので、workflow がなくても本番運用できます
- Teams やメールで自動通知したい場合だけ、通知用 workflow を別途作成して `MANAGER_APPROVAL_TRIGGER_URL` を設定してください
- workflow 実装時は [manager-approval-workflow.md](manager-approval-workflow.md) の `manager_approval_url` と callback token 契約に従ってください
- 詳細は [azure-setup.md](azure-setup.md) を参照してください

## 5.1 Improvement MCP の追加デプロイ

`mcp_server/` は評価起点の改善で使う Azure Functions MCP ツールです。現在は `azd provision` 後の `scripts/postprovision.py` が、Flex Consumption Function App の作成、`mcp_server/` の zip 配備、Functions system key の取得、APIM 側 `improvement-mcp` 登録までを自動で行います。GitHub Actions の `deploy.yml` でも同じ処理を `scripts/deploy_improvement_mcp.py` 経由で再利用します。

既定名を上書きしたい場合の `azd env` 例:

```bash
azd env set IMPROVEMENT_MCP_FUNCTION_APP_NAME func-mcp-<suffix>
azd env set IMPROVEMENT_MCP_FUNCTION_APP_RESOURCE_GROUP rg-dev
azd env set IMPROVEMENT_MCP_STORAGE_ACCOUNT_NAME stfn<suffix>
```

公開 route は引き続き `https://<apim>.azure-api.net/improvement-mcp/runtime/webhooks/mcp` です。この route が一時的に失敗しても、FastAPI は既存の改善ロジックへフォールバックします。

## 5.2 2026-04-05 時点の実機スナップショット

- Azure 上の dev 環境で `/api/health=ok`、`/api/ready=ready` を確認済みです。
- ランタイム用テキスト deployment、評価用 deployment、`gpt-image-1.5` は稼働確認済みです。
- 評価改善フローは APIM 公開 MCP route 経由でも動作確認済みです。
- Fabric 接続、AI Gateway post-provision、post approval actions 用 Logic Apps callback も検証済みです。

## 6. 本番相当の環境変数

### 必須

| 変数名 | 用途 |
| --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | Microsoft Foundry project endpoint |

### よく使う任意変数

| 変数名 | 用途 |
| --- | --- |
| `MODEL_NAME` | テキストモデル deployment 名 |
| `EVAL_MODEL_DEPLOYMENT` | `/api/evaluate` 用の評価モデル deployment 名 |
| `IMPROVEMENT_MCP_ENDPOINT` | APIM 公開 MCP route。`generate_improvement_brief` の呼び出し先 |
| `IMPROVEMENT_MCP_API_KEY` | APIM subscription key など、MCP API に鍵が必要な場合だけ使用 |
| `IMPROVEMENT_MCP_API_KEY_HEADER` | MCP API key のヘッダー名。既定値は `Ocp-Apim-Subscription-Key` |
| `SERVE_STATIC` | FastAPI からビルド済みフロントエンドを返す場合に `true` |
| `API_KEY` | APIM 経由アクセス時の `x-api-key` 保護 |
| `COSMOS_DB_ENDPOINT` | 会話履歴保存 |
| `FABRIC_DATA_AGENT_URL` | Fabric Data Agent Published URL（優先経路） |
| `FABRIC_SQL_ENDPOINT` | Fabric Lakehouse SQL 接続（フォールバック経路） |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析 |
| `IMAGE_PROJECT_ENDPOINT_MAI` | MAI-Image-2 の別 Azure AI / Foundry アカウント endpoint |
| `SPEECH_SERVICE_ENDPOINT` | 動画生成 |
| `SPEECH_SERVICE_REGION` | 動画生成 |
| `VOICE_AGENT_NAME` | Voice Live エージェント名 |
| `VOICE_SPA_CLIENT_ID` | Voice Live MSAL.js 認証 |
| `AZURE_TENANT_ID` | Voice Live 認証 |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後の通知 / 保存 |
| `MANAGER_APPROVAL_TRIGGER_URL` | 任意。Teams / メール通知用 workflow の HTTP trigger。未設定でも共有リンク方式で上司承認を運用可能 |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | テレメトリ |

## 7. デプロイ後の確認

```bash
curl https://<your-app>/api/health
curl https://<your-app>/api/ready
curl -X POST https://<your-app>/api/evaluate \
  -H "Content-Type: application/json" \
  -d '{"query":"沖縄の春キャンペーンを企画して","response":"# 企画書\n...","html":""}'
```

`/api/ready` が `503` の場合、レスポンスの `missing` 配列に不足設定が出ます。

## 8. GitHub Actions の現状

### CI

`ci.yml` は次を実行します。

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run python -m pytest tests/ -v`
4. `cd frontend && npm ci`
5. `cd frontend && npm run lint`
6. `cd frontend && npx tsc --noEmit`
7. `cd frontend && npm run build`

### Deploy

`deploy.yml` は以下の条件で動きます。

- `main` 上の CI 成功後
- または手動 `workflow_dispatch`

処理内容:

1. Azure OIDC ログイン
2. `az acr build`
3. `az containerapp update`
4. 任意で `IMAGE_PROJECT_ENDPOINT_MAI` を Container App に反映
5. 任意で `MANAGER_APPROVAL_TRIGGER_URL` を Container App secret と env に反映
6. 任意で `MAI_RESOURCE_NAME` を使って別 MAI アカウントへの RBAC を bootstrap
7. `/api/health` チェック
8. `/api/ready` チェック

### Security Scan

`security.yml` では Trivy、Gitleaks、npm audit、pip-audit を実行します。

ただし、現状は一部のステップに `continue-on-error` が設定されているため、完全な blocking gate ではなく、結果可視化寄りの運用です。

## 9. よくある詰まりどころ

### `AZURE_AI_PROJECT_ENDPOINT` を入れていない

モック / デモモードになります。Azure 実行確認をしたい場合は設定が必要です。

### Fabric Data Agent がフォールバックに落ちる

`FABRIC_DATA_AGENT_URL` が Fabric の Published URL（`.../aiassistant/openai`）になっているか、Container App の ID に Fabric ワークスペース / Data Agent へのアクセス権があるかを確認してください。未設定または到達不可でもアプリ自体は `FABRIC_SQL_ENDPOINT` → CSV の順で動作を継続します。

### `/api/ready` が `degraded`

`ENVIRONMENT=production` か `staging` で、必須変数が不足しています。

### 評価ボタンが失敗する、または評価値が `score: -1` になる

`AZURE_AI_PROJECT_ENDPOINT` が正しいこと、評価用 deployment を分けるなら `EVAL_MODEL_DEPLOYMENT` が存在することを確認してください。未設定時は `MODEL_NAME` を評価にも使います。

### 画像が透明 PNG で返る

選択中の画像モデルの配備を確認してください:

- **GPT Image 1.5**: `gpt-image-1.5` がメインプロジェクトに配備されていること
- **MAI-Image-2**: `IMAGE_PROJECT_ENDPOINT_MAI` が設定され、別リソースに `MAI-Image-2` が配備され、Container App の Managed Identity にそのリソースへの RBAC が付与されていること

### Azure モードで `approval_request` が出ない

現在は Azure モードでも Agent2 完了後に `approval_request` を返します。出ない場合は `/api/chat` が古い revision のままデプロイされている可能性があります。

### Logic Apps が呼ばれない

IaC で callback URL を注入する構成です。既存環境で未反映の場合は再プロビジョニングまたは Container App 再デプロイを確認してください。

### 改善フローで MCP が使われない

`IMPROVEMENT_MCP_ENDPOINT` が APIM の公開 route `.../improvement-mcp/runtime/webhooks/mcp` になっているかを確認してください。APIM の route 未登録、`x-functions-key` 未転送、subscription key 必須時の `IMPROVEMENT_MCP_API_KEY` 未設定では MCP 呼び出しが失敗しますが、アプリはフォールバックするため UI だけでは気付きにくいです。

現在は、MCP が設定済みなのに呼び出しに失敗した場合、SSE の `tool_event` に `tool=generate_improvement_brief`, `status=failed`, `fallback=legacy_prompt` が出ます。UI やログでこのイベントが見えていれば、APIM か Function App 側の確認対象です。

### 上司承認通知が飛ばない

`MANAGER_APPROVAL_TRIGGER_URL` が未設定、または通知 workflow への送信に失敗しています。この場合でもアプリは manager approval URL を発行するため、待機 UI からリンクを共有すれば承認を継続できます。

### Knowledge Base が静的レスポンスに落ちる

Azure AI Search 接続か `regulations-index` が未整備の可能性があります。`scripts/setup_knowledge_base.py` と Foundry project connection を確認してください。
