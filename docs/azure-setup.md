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
4. **Marketing plan Agent**: Foundry SDK 経由で Agent2 用の事前作成済み Prompt Agent を作成/同期
5. **Entra SPA**: Voice Live + Work IQ delegated auth 用の Entra アプリ登録を作成/再同期（既存 app registration の redirect URI、Graph delegated permissions、Agent 365 Tools scopes を同期）

## 3. Current rebuilt-tenant snapshot (`workiq-dev`, 2026-04-18)

| 領域 | 状態 | 補足 |
| --- | --- | --- |
| Work IQ | 完了 | SPA redirect URI、Graph delegated permissions、**Agent 365 Tools delegated scopes**、admin consent、M365 Copilot ライセンス確認まで完了。既定 runtime は `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool` です。Agent2 は事前作成済み Foundry Agent を使い、per-user delegated token がある場合だけ `source_scope` に応じた read-only Microsoft 365 connector を overlay します。`graph_prefetch` は明示 rollback 用で、必要時だけ Microsoft Graph Copilot Chat API（`chatOverStream` 優先、既定 `WORK_IQ_TIMEOUT_SECONDS=120`）から短い brief を先読みします |
| Search / Foundry IQ | 完了 | Azure AI Search は **East US** に作成。`regulations-index`、`regulations-ks`、`regulations-kb` を投入済み |
| 追加モデル | 完了 | メイン East US 2 Foundry account に `gpt-5-4-mini`、`gpt-4-1-mini`、`gpt-4.1`、`gpt-5.4`、`gpt-image-1.5` を配備済み |
| MAI 経路 | 完了 | 別 East US AI Services account を `IMAGE_PROJECT_ENDPOINT_MAI` へ配線。`MAI-Image-2` deployment 名は `MAI-Image-2e` の alias |
| Fabric | 完了 | Fabric capacity `fcdemojapaneast001` を resume し、workspace `ws-MG-pod2` に `Travel_Lakehouse` と `sales_results` / `customer_reviews` を再投入済み。`FABRIC_DATA_AGENT_URL` / `FABRIC_SQL_ENDPOINT` も Container App へ反映済み |
| Logic Apps / Teams / SharePoint | 部分完了 | Teams connection `teams-1` は Connected。`logic-manager-approval-wmbvhdhcsuyb2` と `logic-wmbvhdhcsuyb2` は live で、post-approval の Teams channel 通知は動作確認済みです。manager approval の signed trigger URL 同期も live で再確認済みで、Container App secret は現在の Logic App callback URL と一致しています。残件は SharePoint 保存経路（site permission grant または connector 再認証） |

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

`gpt-image-1.5` は IaC で自動配備されます。加えて rebuilt `workiq-dev` tenant では **`gpt-4-1-mini` / `gpt-4.1` / `gpt-5.4`** もメイン East US 2 Foundry account に追加済みです。`gpt-image-2` を追加配備する場合は、deployment 名が既定 (`gpt-image-2`) と異なるときだけ `GPT_IMAGE_2_DEPLOYMENT_NAME` を設定してください。

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

### 4.5 上司承認通知 / 承認後アクション (任意)

上司承認ページはアプリに組み込まれています。外部 workflow と連携する場合は、上司通知と承認後アクションの 2 本を分けて設定します:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars \
    MANAGER_APPROVAL_TRIGGER_URL=https://<manager-approval-workflow-url> \
    LOGIC_APP_CALLBACK_URL=https://<post-approval-workflow-url>
```

rebuilt `workiq-dev` tenant ではこの 2 つの URL はすでに live workflow に配線済みです。別 tenant で trigger URL が変わった場合だけ更新してください。

GitHub Actions の `deploy.yml` は manager approval workflow の signed trigger URL を Azure から毎回引き直して Container App secret へ同期します。ローカルの `azd env` は自動同期されないため、`azd up` や手動テストに使う値だけは自分で更新してください。

詳細は [manager-approval-workflow.md](manager-approval-workflow.md) を参照してください。

### 4.6 Work IQ app の admin consent（新テナント必須）

これは **M365 / Entra 側の tenant admin 作業** なので、repo や `azd up` だけでは完了できません。

> rebuilt `workiq-dev` tenant ではすでに完了済みです。以下は **別 tenant を作り直す場合だけ** の手順です。

1. 新テナントの Global Administrator または Cloud Application Administrator で Entra admin center にサインインする
2. SPA アプリ登録に Microsoft Graph delegated permissions を追加する: `Sites.Read.All`, `Mail.Read`, `People.Read.All`, `OnlineMeetingTranscript.Read.All`, `Chat.Read`, `ChannelMessage.Read.All`, `ExternalItem.Read.All`
3. Work IQ / Microsoft 365 Copilot 用の enterprise app / app registration を開き、上記権限に **Grant admin consent** を実行する
4. `foundry_tool` を使うため、同じ SPA app registration に **Agent 365 Tools** の delegated permissions も追加する
   - `McpServers.Mail.All`
   - `McpServers.Calendar.All`
   - `McpServers.Teams.All`
   - `McpServers.OneDriveSharepoint.All`
   - MSAL Browser から token を要求するときは、Microsoft Learn の resources/scopes ガイドどおり `api://<Agent365Tools appId>/McpServers.*` 形式の scope を使う
5. SPA redirect URI にはアプリ本体 URL に加えて **専用 redirect bridge ページ** も登録する
   - `http://localhost:5173/auth-redirect.html`
   - `http://localhost:8000/auth-redirect.html`
   - `https://<container-app-host>/auth-redirect.html`
6. 既定 runtime は **`MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`**。`postprovision.py` が Agent2 用の Foundry Prompt Agent を同期し、実行時はその `agent_reference` を使う
7. `WORKIQ_RUNTIME=foundry_tool` では、事前作成済み Agent に対して read-only の Microsoft 365 connector を per-user delegated token 付きで overlay する。`meeting_notes` は Teams、`emails` は Outlook Email、`teams_chats` は Teams、`documents_notes` は SharePoint を使う。追加の Work IQ endpoint 環境変数は不要
8. `WORKIQ_RUNTIME=graph_prefetch` は明示 rollback 用で、この場合だけ **Microsoft Graph Copilot Chat API**（`POST /beta/copilot/conversations` → `POST /beta/copilot/conversations/{id}/chatOverStream`、必要時 `/chat` へフォールバック）を per-user delegated で呼び出して短い brief を先読みする
9. フロントエンドで Microsoft 365 サインイン後に新しい会話を開始し、preflight の状態が `auth_required` / `consent_required` / `redirecting` から `ready` / `enabled` へ進むこと、そしてバックエンドに保存された `work_iq_session.status` を復元しても同じ UI 状態が表示されることを確認する

tenant-wide consent はユーザー個人の delegated sign-in では代替できないため、この部分だけは外部手順として残ります。

> サインイン確認は **tenant member / guest として参加している Microsoft 365 アカウント**で行ってください。rebuilt tenant に所属しないアカウントは、SPA redirect 後のサインイン自体が拒否されます。

公式ドキュメント:

- [Work IQ overview](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/workiq-overview)
- [Work IQ Tenant Administrator Enablement Guide](https://github.com/microsoft/work-iq/blob/main/ADMIN-INSTRUCTIONS.md)
- [Grant tenant-wide admin consent to an application](https://learn.microsoft.com/en-us/entra/identity/enterprise-apps/grant-admin-consent)
- [Microsoft 365 Copilot minimum requirements](https://learn.microsoft.com/en-us/microsoft-365/copilot/microsoft-365-copilot-minimum-requirements)

### 4.7 Fabric Lakehouse / SQL endpoint の再作成

これは **Fabric workspace / capacity / データ投入が tenant 固有** のため、別 tenant を一から立てる場合は手動です。Azure の resource group には含まれません。

rebuilt `workiq-dev` tenant ではすでに次の構成で復旧済みです。

- capacity: `fcdemojapaneast001`
- workspace: `ws-MG-pod2`
- lakehouse: `Travel_Lakehouse`
- tables: `sales_results`, `customer_reviews`
- data agent: `FFA_DataAgent`

別 tenant で再現する場合:

1. Fabric workspace を作成または復旧する
2. Lakehouse と SQL endpoint を作成し、`sales_results` / `customer_reviews` を投入する
3. 必要に応じて Fabric Data Agent を publish する
4. Container App に `FABRIC_SQL_ENDPOINT` または `FABRIC_DATA_AGENT_URL` を設定する

現行 runtime は **Fabric Data Agent → Fabric SQL endpoint → CSV** の順でフォールバックするため、Data Agent を publish しなくても SQL endpoint だけで実運用できます。

公式ドキュメント:

- [Create a workspace](https://learn.microsoft.com/en-us/fabric/fundamentals/create-workspaces)
- [Create a lakehouse in Microsoft Fabric](https://learn.microsoft.com/en-us/fabric/data-engineering/create-lakehouse)
- [What is the SQL analytics endpoint for a lakehouse?](https://learn.microsoft.com/en-us/fabric/data-engineering/lakehouse-sql-analytics-endpoint)
- [How to connect to the SQL analytics endpoint](https://learn.microsoft.com/en-us/fabric/data-warehouse/how-to-connect)
- [Service principals can use Fabric APIs](https://learn.microsoft.com/en-us/fabric/admin/service-admin-portal-developer)

### 4.8 Teams / SharePoint / Logic Apps の M365 接続

これは **Teams / SharePoint connector の接続が tenant と接続作成者に紐づく** ため、別 tenant や接続切れ環境では Logic Apps designer での再サインインと接続先選択が必要です。

rebuilt `workiq-dev` tenant では、次までは完了済みです。

- Teams connection `teams-1`: **Connected**
- manager approval workflow: `logic-manager-approval-wmbvhdhcsuyb2`
- post-approval workflow: `logic-wmbvhdhcsuyb2`
- `MANAGER_APPROVAL_TRIGGER_URL` / `LOGIC_APP_CALLBACK_URL`: Container App に反映済み（manager approval 側は `deploy.yml` が workflow から full signed URL を再同期）

現在の残件は **SharePoint 保存経路** だけです。優先方針は **Graph + Logic App managed identity + `Sites.Selected`** で、site permission grant が済めば combined branch をそのまま使えます。fallback として `sharepointonline` connector を tenant 側で再認証しても構いません。

別 tenant で再現する場合:

1. Logic Apps designer で Microsoft Teams / SharePoint コネクタを開き、新 tenant のアカウントで接続を作り直す
2. 通知先の Team / channel と保存先の SharePoint site / document library を選び直す
3. SharePoint 保存は、site permission を Logic App managed identity に付与するか、`sharepointonline` connector を再認証する
4. HTTP trigger URL や connection reference が変わった場合は `LOGIC_APP_CALLBACK_URL` / `MANAGER_APPROVAL_TRIGGER_URL` を更新する。GitHub Actions deploy は manager approval workflow の signed URL を自動再同期するが、ローカル実行や `azd up` 用の `azd env` は手で更新が必要

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
| Fabric | Agent1 が Fabric Data Agent または Fabric SQL endpoint を使い、CSV フォールバックにならない |
| 承認後 Teams 通知 | `logic-wmbvhdhcsuyb2` の run history が success で、対象 Team / channel に投稿される |
| 評価 | `/api/evaluate` が `builtin`、`plan_quality`、`asset_quality`、`regression_guard` を返す |

## 7. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| デモモードになる | `AZURE_AI_PROJECT_ENDPOINT` を設定 |
| `/api/ready` が `degraded` | `ENVIRONMENT=production` で必須変数が不足 |
| `gpt-4.1` / `gpt-5.4` が使えない | Azure 側の deployment 名が UI 値 (`gpt-4.1`, `gpt-4-1-mini`, `gpt-5.4`) と一致しているか確認 |
| 画像が透明 PNG | 画像モデル配備を確認。MAI は別 East US account + RBAC が必要。`MAI-Image-2` quota が無い場合は `MAI-Image-2e` を `MAI-Image-2` deployment 名で alias すると現行 backend で利用可能 |
| MCP 改善が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認。新 tenant では Function App が managed identity storage に切り替わっているかも確認する |
| KB が静的レスポンス | `SEARCH_ENDPOINT` / `SEARCH_API_KEY` または Foundry の Azure AI Search 既定接続、`regulations-index` / `regulations-kb` を確認 |
| Work IQ が `timeout` / `completed` にならない | App Insights で Microsoft Graph Copilot Chat API `chatOverStream` / `/chat` のレイテンシを確認し、必要なら `WORK_IQ_TIMEOUT_SECONDS` を 120 以上へ調整する |
| `work_iq_runtime=foundry_tool` がエラーになる | `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` と組み合わせているか、`postprovision.py` で marketing-plan Agent が同期済みか確認する。`legacy` と組み合わせるのは未サポート |
| Work IQ サインインで弾かれる | サインインに使っている Microsoft 365 アカウントが tenant member / guest か確認する。tenant 外アカウントは SPA redirect 後に拒否される |
| 上司通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認し、Container App secret に `?api-version=...&sp=...&sv=...&sig=...` を含む full signed URL が入っているか確かめる。`deploy.yml` の signed URL 再同期が成功しているかも確認する。未設定でも承認ページ自体は動作 |
| 承認後 Teams 通知が飛ばない | `LOGIC_APP_CALLBACK_URL`、`logic-wmbvhdhcsuyb2` の run history、Teams connection `teams-1`、Team / channel ID を確認 |
| SharePoint へ保存されない | target site への permission grant（preferred）または `sharepointonline` connector の認証状態を確認 |
