# Azure セットアップガイド

このガイドは、現在の Bicep とアプリ実装に合わせて、Azure 側で何が自動作成され、何を後から追加設定する必要があるかを整理したものです。

推奨リージョンは East US 2 です。構成図は [azure-architecture.md](azure-architecture.md) を参照してください。

## 1. `azd up` で自動作成されるリソース

| リソース | 現行構成 |
|---|---|
| Resource Group | サブスクリプション配下に環境別で作成 |
| AI Services account | `kind=AIServices`、`allowProjectManagement=true`、`disableLocalAuth=true`、SKU `S0` |
| Microsoft Foundry project | `accounts/projects@2025-06-01` |
| 既定テキストモデル | `gpt-5-4-mini` を `gpt-5.4-mini` から配備 |
| Container Apps Environment | Log Analytics 接続済み |
| Container App | System-assigned MI、`/api/health` と `/api/ready` probe、0-3 replicas |
| Azure Container Registry | Basic SKU |
| API Management | BasicV2、Managed Identity、AI Gateway policy |
| Azure Functions | Flex Consumption、Python 3.13 |
| Logic Apps | Consumption、HTTP trigger ベース |
| Cosmos DB | Serverless、`disableLocalAuth=true`、RBAC、Private Endpoint |
| Key Vault | Private Endpoint、RBAC |
| Log Analytics / Application Insights | 観測基盤 |
| VNet | Container Apps / Private Endpoints 用 |

## 2. `azd up` 後に必要な追加作業

| 項目 | 必要理由 |
|---|---|
| `gpt-image-1.5` の配備 | Agent4 の画像生成 |
| Azure AI Search の作成 | Foundry IQ / `search_knowledge_base()` の実データ検索 |
| `regulations-index` の投入 | 規制ドキュメント検索 |
| Foundry project への Azure AI Search 接続追加 | `connections.get_default(ConnectionType.AZURE_AI_SEARCH)` が前提 |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析ツールで使用 |
| `SPEECH_SERVICE_ENDPOINT` / `SPEECH_SERVICE_REGION` | Promo video 生成で使用 |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後の Logic Apps callback で使用 |
| `TEAMS_WEBHOOK_URL` / `SHAREPOINT_SITE_URL` | Functions 補助ツールで使用 |

## 3. 認証と権限

### アプリ本体

Container App の Managed Identity には Bicep で以下が付与されます。

- Cognitive Services Contributor
- Cognitive Services OpenAI User
- Azure AI Developer
- Azure AI User
- Cognitive Services User
- Cosmos DB Built-in Data Contributor
- Key Vault Secrets User
- AcrPull

アプリ実行時は `DefaultAzureCredential` を使い、以下を呼び出します。

- Microsoft Foundry project endpoint
- Cosmos DB
- Azure AI Search
- Content Safety / Text Analysis

### APIM

APIM の Managed Identity には Foundry バックエンド接続用の Cognitive Services User ロールが必要です。

注: APIM は現状 provision されていますが、アプリの推論トラフィックはまだ直接 project endpoint に向いています。

### Azure AI Search

- 実行時検索: Managed Identity
- 初期インデックス投入: `scripts/setup_knowledge_base.py` で Foundry connection または API key のどちらでも可

## 4. 手順

### 4.1 コアインフラの作成

```bash
azd auth login
azd up
```

### 4.2 配備結果の確認

`azd up` 後に次を確認します。

- `AZURE_AI_PROJECT_ENDPOINT`
- `CONTENT_SAFETY_ENDPOINT`
- `COSMOS_DB_ENDPOINT`
- `AZURE_APIM_GATEWAY_URL`
- `SERVICE_WEB_ENDPOINTS`

### 4.3 `gpt-image-1.5` を追加配備

最新の IaC では `gpt-image-1.5` を自動配備します。既存環境が古いテンプレートで作成されている場合のみ、ポータルまたは CLI で追加してください。

CLI 例:

```bash
az cognitiveservices account deployment create \
  --name <ai-services-account> \
  --resource-group <resource-group> \
  --deployment-name gpt-image-1-5 \
  --model-name gpt-image-1.5 \
  --model-format OpenAI \
  --sku-capacity 1 \
  --sku-name GlobalStandard
```

アプリの `brochure_gen.py` はモデル名 `gpt-image-1.5` を参照します。未配備時は透明 PNG フォールバックです。

### 4.4 Azure AI Search を作成し、規制文書を投入

```bash
az search service create \
  --name <search-name> \
  --resource-group <resource-group> \
  --location eastus2 \
  --sku basic
```

初期投入は次のどちらかで行います。

- Foundry project connection を先に作り、`AZURE_AI_PROJECT_ENDPOINT` を使う
- `SEARCH_ENDPOINT` と `SEARCH_API_KEY` を設定して直接投入する

```bash
uv run python scripts/setup_knowledge_base.py
```

期待されるインデックス名は `regulations-index` です。

### 4.5 Foundry project に Azure AI Search 接続を追加

Foundry ポータルで Azure AI Search connection を追加し、既定 connection にしてください。`regulation_check.py` は次を前提にしています。

- `ConnectionType.AZURE_AI_SEARCH`
- `connections.get_default(...)`

### 4.6 Container App に追加環境変数を入れる

最新の IaC では `CONTENT_UNDERSTANDING_ENDPOINT`、`SPEECH_SERVICE_ENDPOINT`、`SPEECH_SERVICE_REGION`、`LOGIC_APP_CALLBACK_URL` を自動注入します。古い環境や手動更新環境では、必要に応じて以下で上書きしてください。

```bash
az containerapp update \
  --name <container-app-name> \
  --resource-group <resource-group> \
  --set-env-vars \
    CONTENT_UNDERSTANDING_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_REGION=eastus2 \
    LOGIC_APP_CALLBACK_URL=https://<logic-app-trigger-url>
```

  必要に応じて `FABRIC_SQL_ENDPOINT` も追加してください。Fabric Lakehouse / SQL endpoint 自体は引き続き別途構成が必要です。

### 4.7 Functions 補助ツールの環境変数

Functions で以下を使う場合は追加設定します。

- `TEAMS_WEBHOOK_URL`
- `SHAREPOINT_SITE_URL`

未設定でも Functions は動きますが、実送信や実アップロードはスキップされます。

## 5. 検証

### アプリ疎通

```bash
curl https://<container-app-fqdn>/api/health
curl https://<container-app-fqdn>/api/ready
```

### ナレッジベース疎通

- `regulations-index` が存在する
- Foundry project 既定 connection が Azure AI Search を指している
- `search_knowledge_base()` が静的レスポンスではなく検索結果 JSON を返す

### 画像生成疎通

- `gpt-image-1.5` が配備済み
- 画像生成が透明 PNG に落ちていない

### 音声 / 動画疎通

- `SPEECH_SERVICE_ENDPOINT`
- `SPEECH_SERVICE_REGION`

## 6. 実装差分として知っておくこと

- APIM AI Gateway は Azure に作成されるが、アプリ本体の runtime path はまだ direct project endpoint
- 主フローの Azure 実行でも Agent2 完了後に `approval_request` を返し、承認後に Agent3 → Agent4 を続行する
- 品質レビューは主 workflow participant ではなく、主処理後の追加 `text` イベント
- Logic Apps callback URL は IaC から Container App secret として注入する
- Azure AI Search の実行時アクセスは MI だが、bootstrap script には API-key 経路も残る

## 7. 補足

- Azure Functions は Flex Consumption を前提にしています
- 旧 Consumption プランは前提にしていません
