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
2. **Improvement MCP**: Flex Consumption Function App の作成、managed identity ベースの storage 構成、`mcp_server/` zip 配備、APIM `improvement-mcp` route 同期
3. **Voice Agent**: Foundry SDK 経由で Voice Live 対応 Prompt Agent を作成
4. **Entra SPA**: Voice Live + Work IQ delegated auth 用の Entra アプリ登録を作成/再同期（既存 app registration の redirect URI と Graph delegated permissions も同期）

## 3. Current rebuilt-tenant snapshot (`workiq-dev`, 2026-04-17)

| 領域 | 状態 | 補足 |
| --- | --- | --- |
| Work IQ | 完了 | SPA redirect URI、Graph delegated permissions、admin consent、M365 Copilot ライセンス確認まで完了 |
| Search / Foundry IQ | 完了 | Azure AI Search は **East US** に作成。`regulations-index`、`regulations-ks`、`regulations-kb` を投入済み |
| 追加モデル | 完了 | メイン East US 2 Foundry account に `gpt-5-4-mini`、`gpt-4-1-mini`、`gpt-4.1`、`gpt-5.4`、`gpt-image-1.5` を配備済み |
| MAI 経路 | 完了 | 別 East US AI Services account を `IMAGE_PROJECT_ENDPOINT_MAI` へ配線。`MAI-Image-2` deployment 名は `MAI-Image-2e` の alias |
| Fabric | 未完了 | Fabric capacity が inactive のため、Lakehouse / SQL endpoint 再接続が未完 |
| Logic Apps / Teams / SharePoint | 未完了 | 現行 Logic App は HTTP trigger + response の stub。Teams / SharePoint API connection と manager approval workflow は未作成 |

## 4. 手動設定が必要な項目（新しい tenant を一から立てる場合）

### 4.1 Azure AI Search + ナレッジベース投入

```bash
az search service create \
  --name <search-name> \
  --resource-group <rg> \
  --location eastus2 \
  --sku basic

uv run python scripts/setup_knowledge_base.py
```

現在の runtime は **`SEARCH_ENDPOINT` / `SEARCH_API_KEY` を最優先**で使い、未設定時のみ Foundry project の Azure AI Search 既定接続へフォールバックします。したがって、現行実装では Container App へ env/secret を入れればそのまま `search_knowledge_base()` が動きます。Foundry 側の既定接続は任意のフォールバック経路です。

> rebuilt `workiq-dev` tenant では East US 2 に容量が無かったため、Azure AI Search は **East US** に作成済みです。新しい tenant でも East US 2 が失敗したら East US へ切り替えてください。

公式ドキュメント:

- [Foundry IQ (preview)](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/what-is-foundry-iq)
- [Create a knowledge base in Azure AI Search](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-knowledge-base)
- [Connect an Azure AI Search index to Foundry agents](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/ai-search)

### 4.2 画像生成モデル

`gpt-image-1.5` は IaC で自動配備されます。加えて rebuilt `workiq-dev` tenant では **`gpt-4-1-mini` / `gpt-4.1` / `gpt-5.4`** もメイン East US 2 Foundry account に追加済みです。

MAI 系は East US 2 で使えないため、**別の East US AI Services account** にデプロイし:

```bash
azd env set IMAGE_PROJECT_ENDPOINT_MAI https://<mai-account>.services.ai.azure.com
azd env set MAI_RESOURCE_NAME <mai-account-name>
azd provision   # MI に RBAC を付与
```

現行 backend は `POST /mai/v1/images/generations` の `model` フィールドへ **deployment 名**を送ります。そのため、subscription に `MAI-Image-2` quota が無い場合は、`MAI-Image-2e` を `MAI-Image-2` という deployment 名で alias しても互換動作します。rebuild 済み tenant ではこの方式で MAI 経路を有効化しています。

公式ドキュメント:

- [Deploy and use MAI models in Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/use-foundry-models-mai)
- [Deploy Microsoft Foundry Models in the Foundry portal](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/deploy-foundry-models)
- [Foundry Models sold directly by Azure](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure)

### 4.3 Speech / Photo Avatar

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars \
    SPEECH_SERVICE_ENDPOINT=https://<endpoint> \
    SPEECH_SERVICE_REGION=eastus2
```

### 4.4 Fabric Data Agent

Agent1 は `FABRIC_DATA_AGENT_URL` を最優先で使用します:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars FABRIC_DATA_AGENT_URL=https://api.fabric.microsoft.com/v1/workspaces/<ws>/dataagents/<da>/aiassistant/openai
```

利用不可の場合は `FABRIC_SQL_ENDPOINT` → CSV の順でフォールバックします。

### 4.5 上司承認通知 (任意)

上司承認ページはアプリに組み込まれています。Teams/メールの自動通知は別 workflow を作成し:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars MANAGER_APPROVAL_TRIGGER_URL=https://<workflow-url>
```

詳細は [manager-approval-workflow.md](manager-approval-workflow.md) を参照してください。

### 4.6 Work IQ app の admin consent（新テナント必須）

これは **M365 / Entra 側の tenant admin 作業** なので、repo や `azd up` だけでは完了できません。

> rebuilt `workiq-dev` tenant ではすでに完了済みです。以下は **別 tenant を作り直す場合だけ** の手順です。

1. 新テナントの Global Administrator または Cloud Application Administrator で Entra admin center にサインインする
2. SPA アプリ登録に Microsoft Graph delegated permissions を追加する: `Sites.Read.All`, `Mail.Read`, `People.Read.All`, `OnlineMeetingTranscript.Read.All`, `Chat.Read`, `ChannelMessage.Read.All`, `ExternalItem.Read.All`
3. Work IQ / Microsoft 365 Copilot 用の enterprise app / app registration を開き、上記権限に **Grant admin consent** を実行する
4. ランタイムは専用 MCP endpoint ではなく **Microsoft Graph Copilot Chat API**（`POST /beta/copilot/conversations` → `POST /beta/copilot/conversations/{id}/chat`）を per-user delegated で呼び出す。追加の Work IQ endpoint 環境変数は不要
5. フロントエンドで Microsoft 365 サインイン後に新しい会話を開始し、Work IQ の状態が `consent_required` から `ready` / `enabled` へ変わることを確認する

tenant-wide consent はユーザー個人の delegated sign-in では代替できないため、この部分だけは外部手順として残ります。

公式ドキュメント:

- [Work IQ overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/workiq-overview)
- [Work IQ Tenant Administrator Enablement Guide](https://github.com/microsoft/work-iq/blob/main/ADMIN-INSTRUCTIONS.md)
- [Grant tenant-wide admin consent to an application](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)
- [Microsoft 365 Copilot minimum requirements](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-minimum-requirements)

### 4.7 Fabric Lakehouse / SQL endpoint の再作成

これは **Fabric workspace / capacity / データ投入が tenant 固有** のため手動です。Azure の resource group には含まれません。

> 現在の blocker は、旧 workspace がぶら下がっていた Fabric capacity が **Inactive / Suspended** で、Lakehouse API が `CapacityNotActive` を返していることです。加えて、現コードの SQL パスは `Travel_Lakehouse` を前提にしています。

1. 新テナントの Fabric workspace を作成または復旧する
2. Lakehouse と SQL endpoint を再作成し、`sales_results` / `customer_reviews` などのデモデータを投入する
3. 必要に応じて Fabric Data Agent を再公開する
4. Container App に `FABRIC_SQL_ENDPOINT` または `FABRIC_DATA_AGENT_URL` を設定する

完了までは Agent1 は `FABRIC_SQL_ENDPOINT` ではなく CSV フォールバックで動作します。

公式ドキュメント:

- [Create a workspace](https://learn.microsoft.com/en-us/fabric/fundamentals/create-workspaces)
- [Create a lakehouse in Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/data-engineering/create-lakehouse)
- [What is the SQL analytics endpoint for a lakehouse?](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint)
- [How to connect to the SQL analytics endpoint](https://learn.microsoft.com/en-us/fabric/data-warehouse/how-to-connect)
- [Service principals can use Fabric APIs](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer)

### 4.8 Teams / SharePoint / Logic Apps の M365 接続

これは **Teams / SharePoint connector の接続が tenant と接続作成者に紐づく** ため、Logic Apps designer での再サインインと接続先選択が必要です。

> rebuilt `workiq-dev` tenant では `logic-wmbvhdhcsuyb2` は **HTTP trigger + Response だけの stub** で、`Microsoft.Web/connections` は 0 件です。つまり「再接続」以前に Teams / SharePoint connection と manager approval workflow 自体がまだありません。

1. Logic Apps designer で Microsoft Teams / SharePoint コネクタを開き、新テナントのアカウントで接続を作り直す
2. 通知先の Team / channel と保存先の SharePoint site / document library を選び直す
3. HTTP trigger URL や connection reference が変わった場合は `LOGIC_APP_CALLBACK_URL` / `MANAGER_APPROVAL_TRIGGER_URL` を更新する

なお、現在のアーキテクチャでは Foundry から Teams へ直接 publish する構成ではなく、FastAPI → Logic Apps の通知 / 保存フローを前提にしています。

公式ドキュメント:

- [What are connectors in Azure Logic Apps](https://learn.microsoft.com/en-us/azure/connectors/introduction)
- [Secure access and data in workflows](https://learn.microsoft.com/en-us/azure/logic-apps/logic-apps-securing-a-logic-app)
- [Microsoft Teams connector](https://learn.microsoft.com/en-us/connectors/teams/)
- [SharePoint connector](https://learn.microsoft.com/en-us/connectors/sharepoint/)

## 5. 認証と権限

Container App の MI に Bicep で付与されるロール:

- Cognitive Services Contributor / OpenAI User / User
- Azure AI Developer / Azure AI User
- Cosmos DB Built-in Data Contributor
- Key Vault Secrets User / AcrPull

ランタイムは `DefaultAzureCredential` で Foundry, Fabric, Cosmos DB, AI Search を呼び出します。

## 6. 検証チェックリスト

```bash
curl https://<app>/api/health    # → {"status": "ok"}
curl https://<app>/api/ready     # → {"status": "ready", "missing": []}
```

| 確認項目 | 期待動作 |
| --- | --- |
| ナレッジベース | `search_knowledge_base()` が検索結果を返す (静的レスポンスでない) |
| 画像生成 | GPT Image 1.5 と MAI 経路の両方でヒーロー画像が透明 PNG でない |
| 動画生成 | MP4 が返る (SSML ナレーション付き) |
| Voice Live | `/api/voice-config` が MSAL 設定を返す |
| Fabric | Agent1 が CSV フォールバックでない（現 tenant ではまだ未完了） |
| 評価 | `/api/evaluate` が `builtin` + `marketing_quality` を返す |

## 7. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| デモモードになる | `AZURE_AI_PROJECT_ENDPOINT` を設定 |
| `/api/ready` が `degraded` | `ENVIRONMENT=production` で必須変数が不足 |
| `gpt-4.1` / `gpt-5.4` が使えない | Azure 側の deployment 名が UI 値 (`gpt-4.1`, `gpt-4-1-mini`, `gpt-5.4`) と一致しているか確認 |
| 画像が透明 PNG | 画像モデル配備を確認。MAI は別 East US account + RBAC が必要。`MAI-Image-2` quota が無い場合は `MAI-Image-2e` を `MAI-Image-2` deployment 名で alias すると現行 backend で利用可能 |
| MCP 改善が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認。新 tenant では Function App が managed identity storage に切り替わっているかも確認する |
| KB が静的レスポンス | `SEARCH_ENDPOINT` / `SEARCH_API_KEY` または Foundry の Azure AI Search 既定接続、`regulations-index` / `regulations-kb` を確認 |
| 上司通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認。未設定でも承認ページ自体は動作 |
