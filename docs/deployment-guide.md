# デプロイガイド

## 1. 前提条件

| ツール | バージョン |
| --- | --- |
| Python | 3.14+ |
| Node.js | 22+ |
| [uv](https://docs.astral.sh/uv/) | 最新 |
| [Azure CLI](https://learn.microsoft.com/ja-jp/cli/azure/install-azure-cli) | 最新 |
| [Azure Developer CLI (azd)](https://learn.microsoft.com/ja-jp/azure/developer/azure-developer-cli/install-azd) | 最新 |

推奨リージョン: **East US 2**（Code Interpreter 対応リージョン）

> Docker Desktop はローカルビルド時のみ必要です。Azure デプロイは `az acr build` のリモートビルドで行います。

## 2. ローカル開発

### セットアップ

```bash
git clone https://github.com/naoki1213mj/travel-marketing-ai.git
cd travel-marketing-ai
uv sync
cd frontend && npm ci && cd ..
cp .env.example .env
```

`.env` に `AZURE_AI_PROJECT_ENDPOINT` を設定すると Azure 接続モードで動作します。未設定ならデモモードです。

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

## 3. テスト & リント

```bash
uv run pytest
uv run ruff check .
cd frontend && npm run lint
cd frontend && npx tsc --noEmit
cd frontend && npm run build
```

## 4. Docker

```bash
# ローカルビルド
docker build -t travel-agents .
docker run -p 8000:8000 --env-file .env travel-agents

# ACR リモートビルド
az acr build --registry <acr-name> --image travel-agents:latest .
```

Dockerfile はマルチステージ構成です:
- Stage 1: Node.js でフロントエンドビルド
- Stage 2: `python:3.14-slim` + uv で FastAPI + 静的ファイル配信
- non-root ユーザーで実行、`/api/health` ヘルスチェック

## 5. Azure デプロイ (azd)

### 初回

```bash
azd auth login
azd up
```

### 2 回目以降

```bash
azd deploy
```

`azd up` により Bicep でインフラを作成し、ACR リモートビルドと Container Apps デプロイまで自動実行されます。

### postprovision で自動構成される項目

- AI Gateway 接続 (`travel-ai-gateway`) と token policy
- Improvement MCP 用 Function App の作成・managed identity storage 構成・zip 配備・APIM route 登録
- Voice Agent (Prompt Agent) の作成
- Entra SPA アプリ登録 (Voice Live + Work IQ delegated auth 用)

### postprovision 後の手動作業

- 現在の rebuilt `workiq-dev` tenant では **Search/KB**, **Work IQ admin consent**, **gpt-4.1 / gpt-5.4 deployments**, **別 East US MAI endpoint** までは完了済みです
- 新しい tenant を一から立ち上げる場合は、以下の項目が引き続き手動です
- Azure AI Search の作成と `regulations-index` の投入
- Foundry → AI Search 接続の追加
- `FABRIC_DATA_AGENT_URL` / `SPEECH_SERVICE_ENDPOINT` 等の設定
- Work IQ 用 SPA app registration の Graph delegated permissions 追加 + admin consent
- Fabric Lakehouse / SQL endpoint / Fabric Data Agent の新テナント側再作成
- Logic Apps の Teams / SharePoint connector を新テナントで再接続

Work IQ ランタイム連携は専用 MCP endpoint ではなく **Microsoft Graph Copilot Chat API** を per-user delegated token で呼び出します。必要なのは SPA app registration の権限/consent であり、追加の Work IQ API endpoint 環境変数はありません。

詳細は [azure-setup.md](azure-setup.md) を参照してください。

### Current rebuilt-tenant snapshot (`workiq-dev`, 2026-04-17)

| Area | State |
| --- | --- |
| Search / Foundry IQ | Azure AI Search was created in **East US** (East US 2 had no capacity), and `regulations-index`, `regulations-ks`, and `regulations-kb` are already wired into the Container App |
| Work IQ | SPA redirect URIs, Graph delegated permissions, tenant-wide admin consent, and Microsoft 365 Copilot license verification are complete |
| Text models | `gpt-5-4-mini`, `gpt-4-1-mini`, `gpt-4.1`, `gpt-5.4`, and `gpt-image-1.5` exist on the main East US 2 Foundry account |
| MAI image route | A separate East US AI Services account is wired through `IMAGE_PROJECT_ENDPOINT_MAI`; the live `MAI-Image-2` deployment name currently points to the `MAI-Image-2e` model because direct `MAI-Image-2` quota wasn't available |
| Remaining manual work | Fabric capacity / Lakehouse / SQL endpoint rebuild, plus Teams / SharePoint connections and the manager-approval workflow |

### Improvement MCP の追加デプロイ

`postprovision.py` が自動で Function App 作成から APIM 登録まで行います。既定名を上書きしたい場合のみ:

```bash
azd env set IMPROVEMENT_MCP_FUNCTION_APP_NAME func-mcp-<suffix>
azd env set IMPROVEMENT_MCP_FUNCTION_APP_RESOURCE_GROUP rg-dev
azd env set IMPROVEMENT_MCP_STORAGE_ACCOUNT_NAME stfn<suffix>
```

## 6. 環境変数

| 変数名 | 必須 | 用途 |
| --- | --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | 本番 | Microsoft Foundry project endpoint |
| `MODEL_NAME` | 任意 | テキスト deployment 名 (既定: `gpt-5-4-mini`) |
| `EVAL_MODEL_DEPLOYMENT` | 推奨 | 評価用の専用 deployment |
| `COSMOS_DB_ENDPOINT` | 任意 | 会話履歴保存 |
| `SEARCH_ENDPOINT` | 任意 | Azure AI Search endpoint (`search_knowledge_base()` はこれを最優先で使う) |
| `SEARCH_API_KEY` | 任意 | Azure AI Search 管理キー。live tenant では Container Apps secret で保持 |
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent Published URL |
| `FABRIC_SQL_ENDPOINT` | 任意 | Fabric SQL フォールバック |
| `IMPROVEMENT_MCP_ENDPOINT` | 任意 | APIM MCP ルート |
| `WORK_IQ_TIMEOUT_SECONDS` | 任意 | Graph Copilot Chat API 取得 timeout（秒、既定 10） |
| `IMAGE_PROJECT_ENDPOINT_MAI` | 任意 | 別の MAI 対応 AI Services endpoint |
| `SPEECH_SERVICE_ENDPOINT` | 任意 | Photo Avatar 動画生成 |
| `SPEECH_SERVICE_REGION` | 任意 | Speech リージョン |
| `LOGIC_APP_CALLBACK_URL` | 任意 | 承認後アクション |
| `MANAGER_APPROVAL_TRIGGER_URL` | 任意 | 上司承認通知 workflow |
| `SERVE_STATIC` | 任意 | コンテナ内フロントエンド配信 (`true`) |
| `API_KEY` | 任意 | API エンドポイント保護 |

全項目は [.env.example](../.env.example) を参照してください。

## 7. デプロイ後の確認

```bash
curl https://<your-app>/api/health
curl https://<your-app>/api/ready
```

`/api/ready` が `503` の場合、レスポンスの `missing` 配列に不足設定が表示されます。

## 8. CI/CD (GitHub Actions)

### CI (`ci.yml`)

Ruff lint → pytest (277 tests) → npm lint → tsc → npm build

### Deploy (`deploy.yml`)

1. Azure OIDC ログイン
2. `az acr build`
3. `az containerapp update`
4. `/api/health` + `/api/ready` チェック

### Security (`security.yml`)

Trivy, Gitleaks, npm audit, pip-audit

## 9. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| デモモードになる | `AZURE_AI_PROJECT_ENDPOINT` を設定 |
| `/api/ready` が `degraded` | `ENVIRONMENT=production` で必須変数が不足 |
| `gpt-4.1` / `gpt-5.4` が使えない | Azure 側の deployment 名が UI 値と一致しているか確認 (`gpt-4.1`, `gpt-4-1-mini`, `gpt-5.4`) |
| 画像が透明 PNG | `IMAGE_PROJECT_ENDPOINT_MAI` と別 East US MAI account の RBAC を確認。`MAI-Image-2` quota が無い subscription では `MAI-Image-2e` を `MAI-Image-2` deployment 名で alias すると現行 backend で利用可能 |
| MCP が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認 |
| 上司承認通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認。未設定でも承認ページ自体は動作 |
| KB が静的レスポンス | `SEARCH_ENDPOINT` / `SEARCH_API_KEY` または Foundry の Azure AI Search 既定接続、`regulations-index` / `regulations-kb` を確認 |
