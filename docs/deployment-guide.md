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
- Improvement MCP 用 Function App の作成・zip 配備・APIM route 登録
- Voice Agent (Prompt Agent) の作成
- Entra SPA アプリ登録 (Voice Live 認証用)

### postprovision 後の手動作業

- Azure AI Search の作成と `regulations-index` の投入
- Foundry → AI Search 接続の追加
- `FABRIC_DATA_AGENT_URL` / `SPEECH_SERVICE_ENDPOINT` 等の設定

詳細は [azure-setup.md](azure-setup.md) を参照してください。

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
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent Published URL |
| `FABRIC_SQL_ENDPOINT` | 任意 | Fabric SQL フォールバック |
| `IMPROVEMENT_MCP_ENDPOINT` | 任意 | APIM MCP ルート |
| `IMAGE_PROJECT_ENDPOINT_MAI` | 任意 | MAI-Image-2 用の別 Foundry アカウント |
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

Ruff lint → pytest → npm lint → tsc → npm build

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
| 画像が透明 PNG | 画像モデルの配備を確認。MAI-Image-2 は別リソース + RBAC が必要 |
| MCP が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認 |
| 上司承認通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認。未設定でも承認ページ自体は動作 |
| KB が静的レスポンス | AI Search 接続と `regulations-index` を確認 |
