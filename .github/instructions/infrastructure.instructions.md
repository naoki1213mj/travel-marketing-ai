---
name: 'インフラ構成ルール'
description: 'Bicep, azd, Dockerfile, CI/CD の規約'
applyTo: 'infra/**, azure.yaml, **/Dockerfile, .github/workflows/**'
---

## インフラ構成ルール

### Bicep / IaC

- リソースは `infra/modules/` にモジュール分割。`infra/main.bicep` でオーケストレーション
- リソース名にサブスクリプション ID やテナント ID をハードコードしない
- パラメータで環境名（dev/prod）を切り替える
- Managed Identity を全リソースで使う。API キーベースの認証は禁止

### azd

- `azure.yaml` でサービス定義。`azd up` で一発デプロイ
- 環境変数は `azd env set` で管理。`.azure/` は .gitignore に含める

### Dockerfile（マルチステージ）

- Stage 1: Node.js でフロントエンドビルド (`npm ci && npm run build`)
- Stage 2: Python で FastAPI + 静的ファイル配信
- ベースイメージ: `python:3.14-slim`
- uv でパッケージインストール。pip は使わない
- HEALTHCHECK: `/api/health` を定義する

### GitHub Actions (DevSecOps)

- `ci.yml`: Ruff lint → pytest → tsc --noEmit → npm run build
- `deploy.yml`: OIDC Login → az acr build → az containerapp update → Health check
- `security.yml`: Trivy → Gitleaks → npm audit + pip-audit
- 認証は OIDC Workload Identity Federation。シークレット不要
- GitHub Variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`

### Azure リソース構成

- Container Apps: VNet 統合 (snet-container-apps)
- Key Vault: Private Endpoint (`publicNetworkAccess: Disabled`)
- Azure Functions: Flex Consumption プラン（旧 Consumption はレガシー）
- APIM: リバースプロキシとして使用（Foundry 統合 AI Gateway は Preview）
- Foundry: Basic Setup（Hosted Agent が private networking 未対応のため）
