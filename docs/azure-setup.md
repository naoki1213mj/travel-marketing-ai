# Azure セットアップガイド

推奨リージョン: **East US 2**。構成図は [azure-architecture.md](azure-architecture.md) を参照してください。

## 1. azd up で自動作成されるリソース

| リソース | 構成 |
| --- | --- |
| AI Services | `kind=AIServices`, `allowProjectManagement=true`, `gpt-5-4-mini` 自動配備 |
| Foundry Project | `accounts/projects@2025-06-01` |
| Container Apps | System MI, health/readiness probe, 0–3 replicas |
| APIM | BasicV2, Managed Identity, AI Gateway policy |
| Logic Apps | Consumption, HTTP trigger (post-approval actions) |
| Cosmos DB | Serverless, Private Endpoint, RBAC |
| Key Vault | Private Endpoint, RBAC |
| Log Analytics / App Insights | 観測基盤 |
| VNet | Container Apps / Private Endpoints 用 |

## 2. postprovision で自動構成される項目

`azd up` 完了後に `scripts/postprovision.py` が自動実行します:

1. **AI Gateway**: Foundry に `travel-ai-gateway` APIM 接続を作成、token policy 適用
2. **Improvement MCP**: Flex Consumption Function App の作成、`mcp_server/` zip 配備、APIM `improvement-mcp` route 同期
3. **Voice Agent**: Foundry に Voice Live 対応 Prompt Agent を作成
4. **Entra SPA**: Voice Live 認証用の Entra アプリ登録を作成

## 3. 手動設定が必要な項目

### 3.1 Azure AI Search + ナレッジベース投入

```bash
az search service create \
  --name <search-name> \
  --resource-group <rg> \
  --location eastus2 \
  --sku basic

uv run python scripts/setup_knowledge_base.py
```

インデックス名: `regulations-index`。Foundry ポータルでこの Search を既定接続として追加してください。

### 3.2 画像生成モデル

`gpt-image-1.5` は IaC で自動配備されます。MAI-Image-2 は別リソースにデプロイし:

```bash
azd env set IMAGE_PROJECT_ENDPOINT_MAI https://<mai-account>.services.ai.azure.com
azd env set MAI_RESOURCE_NAME <mai-account-name>
azd provision   # MI に RBAC を付与
```

### 3.3 Speech / Photo Avatar

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars \
    SPEECH_SERVICE_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_REGION=eastus2
```

### 3.4 Fabric Data Agent

Agent1 は `FABRIC_DATA_AGENT_URL` を最優先で使用します:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars FABRIC_DATA_AGENT_URL=https://api.fabric.microsoft.com/v1/workspaces/<ws>/dataagents/<da>/aiassistant/openai
```

利用不可の場合は `FABRIC_SQL_ENDPOINT` → CSV の順でフォールバックします。

### 3.5 上司承認通知 (任意)

上司承認ページはアプリに組み込まれています。Teams/メールの自動通知は別 workflow を作成し:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars MANAGER_APPROVAL_TRIGGER_URL=https://<workflow-url>
```

詳細は [manager-approval-workflow.md](manager-approval-workflow.md) を参照してください。

## 4. 認証と権限

Container App の MI に Bicep で付与されるロール:

- Cognitive Services Contributor / OpenAI User / User
- Azure AI Developer / Azure AI User
- Cosmos DB Built-in Data Contributor
- Key Vault Secrets User / AcrPull

ランタイムは `DefaultAzureCredential` で Foundry, Fabric, Cosmos DB, AI Search を呼び出します。

## 5. 検証チェックリスト

```bash
curl https://<app>/api/health    # → {"status": "ok"}
curl https://<app>/api/ready     # → {"status": "ready", "missing": []}
```

| 確認項目 | 期待動作 |
| --- | --- |
| ナレッジベース | `search_knowledge_base()` が検索結果を返す (静的レスポンスでない) |
| 画像生成 | ヒーロー画像が透明 PNG でない |
| 動画生成 | MP4 が返る (SSML ナレーション付き) |
| Voice Live | `/api/voice-config` が MSAL 設定を返す |
| Fabric | Agent1 が CSV フォールバックでない |
| 評価 | `/api/evaluate` が `builtin` + `marketing_quality` を返す |

## 6. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| デモモードになる | `AZURE_AI_PROJECT_ENDPOINT` を設定 |
| `/api/ready` が `degraded` | `ENVIRONMENT=production` で必須変数が不足 |
| 画像が透明 PNG | 画像モデル配備を確認。MAI は別リソース + RBAC |
| MCP 改善が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認。`tool_event` に `status=failed` が出ていないか確認 |
| KB が静的レスポンス | AI Search 接続と `regulations-index` を確認 |
| 上司通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認。未設定でも承認ページ自体は動作 |
