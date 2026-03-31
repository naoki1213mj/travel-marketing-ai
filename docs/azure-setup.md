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
| 画像生成モデル | `gpt-image-1-5` を `gpt-image-1.5` から配備 |
| Container Apps Environment | Log Analytics 接続済み |
| Container App | System-assigned MI、`/api/health` と `/api/ready` probe、0-3 replicas |
| Azure Container Registry | Basic SKU |
| API Management | BasicV2、Managed Identity、AI Gateway policy |
| Logic Apps | Consumption、HTTP trigger ベース |
| Cosmos DB | Serverless、`disableLocalAuth=true`、RBAC、Private Endpoint |
| Key Vault | Private Endpoint、RBAC |
| Log Analytics / Application Insights | 観測基盤 |
| VNet | Container Apps / Private Endpoints 用 |

## 2. `azd up` 後に必要な追加作業

| 項目 | 必要理由 |
|---|---|
| Azure AI Search の作成 | Foundry IQ / `search_knowledge_base()` の実データ検索 |
| `regulations-index` の投入 | 規制ドキュメント検索 |
| Foundry project への Azure AI Search 接続追加 | `connections.get_default(ConnectionType.AZURE_AI_SEARCH)` が前提 |
| `FABRIC_SQL_ENDPOINT` の設定 | Agent1 の Fabric Lakehouse リアルタイムデータ検索（未設定時は CSV フォールバック） |
| `CONTENT_UNDERSTANDING_ENDPOINT` | PDF 解析ツールで使用 |
| `SPEECH_SERVICE_ENDPOINT` / `SPEECH_SERVICE_REGION` | Photo Avatar 動画生成で使用（`casual-sitting` スタイル） |
| `VOICE_SPA_CLIENT_ID` / `AZURE_TENANT_ID` | Voice Live の MSAL.js 認証（Entra アプリ登録が必要） |
| `LOGIC_APP_CALLBACK_URL` | 承認継続後の Logic Apps callback で使用 |

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

### 4.3 postprovision フック

`azd up` 完了後に `scripts/postprovision.py` が自動実行されます。このスクリプトは以下を行います:

1. **AI Gateway 接続の作成**: Foundry project に `travel-ai-gateway` という名前の APIM 接続を作成し、`ProjectManagedIdentity` 認証を設定
2. **トークン制限ポリシーの適用**: APIM の foundry-* API に `llm-token-limit`（80,000 tokens/min）と `llm-emit-token-metric` ポリシーを適用
3. **Voice Agent の作成**: Foundry に Voice Live 対応のプロンプトエージェントを作成（Entra アプリ登録の設定を含む）

`AZURE_APIM_NAME` が未設定の場合、APIM 関連の設定はスキップされます。スクリプトは冪等で、複数回実行しても安全です。

### 4.4 画像生成モデルの確認

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

### 4.5 Azure AI Search を作成し、規制文書を投入

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

### 4.6 Foundry project に Azure AI Search 接続を追加

Foundry ポータルで Azure AI Search connection を追加し、既定 connection にしてください。`regulation_check.py` は次を前提にしています。

- `ConnectionType.AZURE_AI_SEARCH`
- `connections.get_default(...)`

### 4.7 Container App に追加環境変数を入れる

最新の IaC では `CONTENT_UNDERSTANDING_ENDPOINT`、`SPEECH_SERVICE_ENDPOINT`、`SPEECH_SERVICE_REGION`、`LOGIC_APP_CALLBACK_URL` を自動注入します。古い環境や手動更新環境では、必要に応じて以下で上書きしてください。

```bash
az containerapp update \
  --name <container-app-name> \
  --resource-group <resource-group> \
  --set-env-vars \
    CONTENT_UNDERSTANDING_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_REGION=eastus2 \
    LOGIC_APP_CALLBACK_URL=https://<logic-app-trigger-url> \
    FABRIC_SQL_ENDPOINT=<fabric-sql-endpoint>
```

### 4.8 Fabric Lakehouse の設定

Agent1 が Fabric Lakehouse にリアルタイム接続するには以下が必要です:

1. Fabric Lakehouse に売上テーブル（`sales_history`）とレビューテーブル（`customer_reviews`）を作成
2. SQL endpoint を取得（Fabric ポータル → Lakehouse → SQL analytics endpoint）
3. Container App の Managed Identity に Fabric SQL への読み取り権限を付与
4. `FABRIC_SQL_ENDPOINT` 環境変数を設定

```bash
az containerapp update \
  --name <container-app-name> \
  --resource-group <resource-group> \
  --set-env-vars FABRIC_SQL_ENDPOINT=<fabric-sql-endpoint>
```

接続は pyodbc + Azure AD トークン認証（`SQL_COPT_SS_ACCESS_TOKEN`）で行います。`FABRIC_SQL_ENDPOINT` 未設定時は CSV ファイル (`data/sales_history.csv`, `data/customer_reviews.csv`) にフォールバックします。

### 4.9 Voice Live の設定（Entra アプリ登録）

Voice Live API のフロントエンド認証には Entra アプリ登録が必要です:

1. Azure Portal → Entra ID → App registrations → New registration
2. Redirect URI に `http://localhost:5173`（開発）と `https://<container-app-fqdn>`（本番）を SPA として追加
3. API permissions に `https://cognitiveservices.azure.com/user_impersonation` を追加

環境変数:

```bash
az containerapp update \
  --name <container-app-name> \
  --resource-group <resource-group> \
  --set-env-vars \
    VOICE_SPA_CLIENT_ID=<entra-app-client-id> \
    AZURE_TENANT_ID=<entra-tenant-id>
```

`scripts/postprovision.py` が Foundry に Voice Live 対応のプロンプトエージェントを自動作成します。

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

- `SPEECH_SERVICE_ENDPOINT` が設定済み
- `SPEECH_SERVICE_REGION` が設定済み
- Photo Avatar 動画生成が動作する（`casual-sitting` スタイル、MP4 出力）

### Voice Live 疎通

- `VOICE_SPA_CLIENT_ID` が設定済み
- `AZURE_TENANT_ID` が設定済み
- `/api/voice-config` が MSAL 設定を返す
- フロントエンドの VoiceInput コンポーネントで音声入力が動作する
- Voice Live 利用不可時に Web Speech API にフォールバックする

### Fabric Lakehouse 疎通

- `FABRIC_SQL_ENDPOINT` が設定済み
- Agent1 の `search_sales_history()` が Fabric SQL クエリ結果を返す（CSV フォールバックではない）
- Managed Identity に Fabric SQL 読み取り権限が付与済み

## 6. 実装差分として知っておくこと

- APIM AI Gateway は Azure に作成され、`scripts/postprovision.py` で Foundry AI Gateway 接続（`travel-ai-gateway`）とポリシーが自動構成される
- Agent1 は Fabric Lakehouse SQL endpoint にリアルタイム接続し、CSV は フォールバック専用
- Agent4 は顧客向けブローシャを生成し、KPI・社内分析を含めない
- Agent5（動画生成）は Photo Avatar で `casual-sitting` スタイル、`ja-JP-NanamiNeural` 音声の販促動画を生成
- Agent6 は `GitHubCopilotAgent` + `PermissionHandler.approve_all` で動作
- Code Interpreter は自動検出でグレースフルフォールバック
- Voice Live API は MSAL.js + Entra アプリ登録で認証。Web Speech API への自動フォールバックあり
- 会話履歴は Cosmos DB から `restoreConversation()` で再推論なしに復元
- 主フローの Azure 実行でも Agent2 完了後に `approval_request` を返し、承認後に Agent3a → Agent3b → Agent4 → Agent5 を続行する
- パイプラインは 5 ユーザー向けステップで、内部は 7 エージェントで構成（Agent3a+3b がステップ 4、Agent4+5 がステップ 5 を共有）
- 品質レビュー（Agent6）は主 workflow participant ではなく、主処理後の追加 `text` イベント
- Logic Apps callback URL は IaC から Container App secret として注入する
- Azure AI Search の実行時アクセスは MI だが、bootstrap script には API-key 経路も残る

## 7. 補足

- `gpt-image-1.5` は IaC で自動配備される。古いテンプレートで作成済みの環境のみ手動追加が必要
