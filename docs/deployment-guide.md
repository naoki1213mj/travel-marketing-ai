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
git clone https://github.com/naoki1213mj/hackathon-teamD.git
cd hackathon-teamD
uv sync
cd frontend && npm ci && cd ..
cp .env.example .env
```

最小限の Azure 接続を使う場合は、`.env` に以下を設定します。

```env
AZURE_AI_PROJECT_ENDPOINT=https://your-foundry.services.ai.azure.com/api/projects/your-project
CONTENT_SAFETY_ENDPOINT=https://your-foundry.cognitiveservices.azure.com/
```

`AZURE_AI_PROJECT_ENDPOINT` を入れなければモック / デモモードで動作します。

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
- Container App には Content Understanding / Speech / Logic Apps callback の基本設定も自動注入されます
- post-provision で残る主作業は Azure AI Search の接続・`regulations-index` の投入、および必要に応じた `FABRIC_SQL_ENDPOINT` の設定です
- 詳細は [azure-setup.md](azure-setup.md) を参照してください

## 6. 本番相当の環境変数

### 必須

| 変数名 | 用途 |
|---|---|
| `AZURE_AI_PROJECT_ENDPOINT` | Microsoft Foundry project endpoint |
| `CONTENT_SAFETY_ENDPOINT` | Content Safety / Text Analysis endpoint |

### よく使う任意変数

| 変数名 | 用途 |
|---|---|
| `MODEL_NAME` | テキストモデル deployment 名 |
| `COSMOS_DB_ENDPOINT` | 会話履歴保存 |
| `FABRIC_SQL_ENDPOINT` | Fabric Lakehouse 接続 |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析 |
| `SPEECH_SERVICE_ENDPOINT` | 動画生成 |
| `SPEECH_SERVICE_REGION` | 動画生成 |
| `VOICE_SPA_CLIENT_ID` | Voice Live MSAL.js 認証 |
| `AZURE_TENANT_ID` | Voice Live 認証 |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後の通知 / 保存 |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | テレメトリ |

## 7. デプロイ後の確認

```bash
curl https://<your-app>/api/health
curl https://<your-app>/api/ready
```

`/api/ready` が `503` の場合、レスポンスの `missing` 配列に不足設定が出ます。

## 8. GitHub Actions の現状

### CI

`ci.yml` は次を実行します。

1. `uv sync --frozen`
2. `uv run ruff check .`
3. `uv run python -m pytest tests/ -v`
4. `cd frontend && npm install --no-package-lock`
5. `cd frontend && npm run lint`
6. `cd frontend && npx tsc --noEmit`
7. `cd frontend && npm run build`

注: Ubuntu 上の rolldown binding 互換性のため、CI では `npm ci` ではなく `npm install --no-package-lock` を使っています。

### Deploy

`deploy.yml` は以下の条件で動きます。

- `main` 上の CI 成功後
- または手動 `workflow_dispatch`

処理内容:

1. Azure OIDC ログイン
2. `az acr build`
3. `az containerapp update`
4. `/api/health` チェック
5. `/api/ready` チェック

### Security Scan

`security.yml` では Trivy、Gitleaks、npm audit、pip-audit を実行します。

ただし、現状は一部のステップに `continue-on-error` が設定されているため、完全な blocking gate ではなく、結果可視化寄りの運用です。

## 9. よくある詰まりどころ

### `AZURE_AI_PROJECT_ENDPOINT` を入れていない

モック / デモモードになります。Azure 実行確認をしたい場合は設定が必要です。

### `/api/ready` が `degraded`

`ENVIRONMENT=production` か `staging` で、必須変数が不足しています。

### 画像が透明 PNG で返る

`gpt-image-1.5` の配備がないか、画像生成が失敗しています。

### Azure モードで `approval_request` が出ない

現在は Azure モードでも Agent2 完了後に `approval_request` を返します。出ない場合は `/api/chat` が古い revision のままデプロイされている可能性があります。

### Logic Apps が呼ばれない

IaC で callback URL を注入する構成です。既存環境で未反映の場合は再プロビジョニングまたは Container App 再デプロイを確認してください。

### Knowledge Base が静的レスポンスに落ちる

Azure AI Search 接続か `regulations-index` が未整備の可能性があります。`scripts/setup_knowledge_base.py` と Foundry project connection を確認してください。
