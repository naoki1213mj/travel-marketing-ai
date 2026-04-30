# Azure セットアップガイド

推奨リージョン: **East US 2**。構成図は [azure-architecture.md](azure-architecture.md) を参照してください。

## 1. azd up で自動作成されるリソース

| リソース | 構成 |
| --- | --- |
| AI Services | `kind=AIServices`, `allowProjectManagement=true`, `gpt-5-4-mini` 自動配備 |
| Foundry Project | `accounts/projects@2025-06-01` |
| Container Apps | System MI, health/readiness probe, optional VNet-integrated CAE (`snet-container-apps`) for approved private-network migration, approval-controlled scale-out |
| APIM | BasicV2, Managed Identity, AI Gateway policy |
| Logic Apps | Consumption, HTTP trigger (post-approval actions) |
| Cosmos DB | Serverless, Private Endpoint, RBAC |
| Key Vault | Private Endpoint, RBAC |
| Log Analytics / App Insights | 観測基盤 |
| VNet | Container Apps / Private Endpoints 用 |

## 2. postprovision で自動構成される項目

`azd up` 完了後に `scripts/postprovision.py` が自動実行します:

1. **AI Gateway**: Foundry に `travel-ai-gateway` APIM 接続を作成、token policy 適用
2. **Improvement MCP**: Flex Consumption Function App の作成、managed identity ベースの storage 構成、vendored 依存入り ready-to-run zip 配備、MCP runtime 応答確認、APIM `improvement-mcp` route 同期
3. **Voice Agent**: Foundry SDK 経由で Voice Live 対応 Prompt Agent を作成
4. **Marketing plan Agent**: Foundry SDK 経由で Agent2 用の事前作成済み Prompt Agent を作成/同期（UI で選択できる `gpt-5-4-mini` / `gpt-5.4` / `gpt-4-1-mini` / `gpt-4.1` をまとめて同期）
5. **Entra SPA**: Voice Live + Work IQ delegated auth 用の Entra アプリ登録を作成/再同期（既存 app registration の redirect URI、Voice Live / Foundry delegated scopes、`graph_prefetch` rollback 用の Graph delegated permissions を同期）

## 3. Current rebuilt-tenant snapshot (`workiq-dev`, 2026-04-18)

| 領域 | 状態 | 補足 |
| --- | --- | --- |
| Work IQ | 完了 | SPA redirect URI、Foundry delegated auth (`https://ai.azure.com/user_impersonation`)、`graph_prefetch` rollback 用 Graph delegated permissions、admin consent、M365 Copilot ライセンス確認まで完了。既定 runtime は `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool` です。Agent2 は事前作成済み Foundry Agent を使い、backend がユーザーの Foundry delegated token を Responses API へ渡して、添付済みの Work IQ MCP connection を per-user で実行します。`graph_prefetch` は明示 rollback 用で、必要時だけ Microsoft Graph Copilot Chat API（`chatOverStream` 優先、既定 `WORK_IQ_TIMEOUT_SECONDS=120`）から短い brief を先読みします |
| Search / Foundry IQ | 完了 | Azure AI Search は **East US** に作成。`regulations-index`、`regulations-ks`、`regulations-kb` を投入済み |
| 追加モデル | 完了 / 要 quota | メイン East US 2 Foundry account に `gpt-5-4-mini`、`gpt-4-1-mini`、`gpt-4.1`、`gpt-5.4` を配備済み。`gpt-5.5` は East US 2 catalog で GA（version `2026-04-24`、`GlobalStandard` / `DataZoneStandard`、Responses 対応）ですが、現 subscription の quota は 0 TPM なので未配備です。アプリ既定の画像経路は `gpt-image-2` で、GPT 系画像モデルは `AZURE_AI_PROJECT_ENDPOINT` から導出した AI Services account endpoint の Azure OpenAI Images API を Managed Identity で呼びます。deployment 名が既定と異なる場合は `GPT_IMAGE_2_DEPLOYMENT_NAME` で上書きできます。`gpt-image-1.5` も引き続き利用可能です |
| MAI 経路 | 完了 | 別 East US AI Services account を `IMAGE_PROJECT_ENDPOINT_MAI` へ配線。`MAI-Image-2` deployment 名は `MAI-Image-2e` の alias |
| Fabric | 完了 | Fabric capacity `fcdemojapaneast001` を resume し、workspace `ws-MG-pod2` に `Travel_Lakehouse` と `sales_results` / `customer_reviews` を再投入済み。`FABRIC_DATA_AGENT_URL` / `FABRIC_SQL_ENDPOINT` も Container App へ反映済み |
| Logic Apps / Teams / SharePoint | 部分完了 | Teams connection `teams-1` は Connected。`logic-manager-approval-wmbvhdhcsuyb2` と `logic-wmbvhdhcsuyb2` は live で、post-approval の Teams channel 通知は動作確認済みです。manager approval の signed trigger URL 同期も live で再確認済みで、Container App secret は現在の Logic App callback URL と一致しています。残件は SharePoint 保存経路（site permission grant または connector 再認証） |
| Rollout gates | default-off | `/api/capabilities` は secret を含まない boolean 状態だけを返します。Source ingestion、MAI Transcribe、継続監視、cost metrics、gpt-5.5、Model Router はそれぞれ feature flag と必須 endpoint/deployment/quota が揃うまで UI で production-ready と扱いません |


### 4.0 Container Apps / Cosmos DB private endpoint migration note

IaC places the Container Apps Environment in the dedicated `snet-container-apps` subnet when both `ENABLE_CONTAINER_APPS_VNET_INTEGRATION=true` and `CONTAINER_APPS_VNET_INTEGRATION_MIGRATION_APPROVAL=CONFIRM_CAE_VNET_MIGRATION` are set, and attaches the Cosmos DB / Key Vault private endpoints to their `privatelink.*` zones linked to the same VNet. Cosmos DB stays `publicNetworkAccess: Disabled`.

Azure Container Apps Environment VNet integration is a create-time setting and `managedEnvironmentId` is immutable on the Container App. Therefore the IaC implements **blue-green** rather than in-place migration: when the two flags above are approved, both the CAE and the Container App are renamed with a `-pn` (private network) suffix (e.g. `cae-<token>-pn`, `ca-<token>-pn`) so a fresh VNet-integrated environment is created side-by-side and `azd` retargets the `web` service to the new Container App. The original `cae-<token>` / `ca-<token>` resources are not in Bicep anymore and must be deleted manually after a stability window.

Operational notes for the migration:

- The first `azd provision` after flipping the flags creates the new CAE and Container App in `snet-container-apps`. The CA module emits AcrPull and Key Vault Secrets User role assignments; if the initial revision pull races the role-assignment propagation, a second `azd provision` will succeed once the assignments exist.
- Validate that the CA resolves Cosmos / Key Vault through `privatelink.documents.azure.com` / `privatelink.vaultcore.azure.net` from inside the VNet-integrated environment (`getent hosts cosmos-<token>.documents.azure.com` should return a `10.0.x.x` private IP) before raising `CONTAINER_APP_MAX_REPLICAS` above the default `1`.
- The blue-green cutover preserves SPA Redirect URIs by union: `scripts/postprovision.py::_ensure_spa_redirect_uris` merges the old and new Container App FQDNs into the `travel-voice-spa` Entra app, so both keep working until the old CA is decommissioned.
- After 1–2 hours of stable traffic on the new FQDN, the old environment can be drained with `az containerapp delete -n ca-<token> -g <rg> --yes` followed by `az containerapp env delete -n cae-<token> -g <rg> --yes`.

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

アプリ既定の画像経路は `gpt-image-2` です。rebuilt `workiq-dev` tenant では **`gpt-4-1-mini` / `gpt-4.1` / `gpt-5.4`** をメイン East US 2 Foundry account に追加済みで、`gpt-image-2` は既定 deployment 名 (`gpt-image-2`) で配備するか、異なる場合だけ `GPT_IMAGE_2_DEPLOYMENT_NAME` を設定してください。GPT 系画像モデルは project endpoint ではなく、そこから導出した AI Services account endpoint に対して Azure OpenAI Images API を呼びます。認証は Container Apps managed identity の Entra token で、429 / timeout / 5xx / connection error は上限付き retry/backoff します。`gpt-image-1.5` は互換 fallback として残しています。

`gpt-5.5` は Microsoft Foundry の East US 2 catalog に GA として表示されますが、rebuilt `workiq-dev` subscription では `OpenAI.GlobalStandard.gpt-5.5` と `OpenAI.DataZoneStandard.gpt-5.5` の quota がどちらも 0 TPM です。quota が付与されたら、同じ AI Services account に `gpt-5.5` deployment を作成し、必要に応じて `MODEL_NAME=gpt-5.5` を設定してください。postprovision は optional model として `gpt-5.5` の marketing-plan Prompt Agent 同期を試みますが、deployment 未作成の場合は既存モデルの同期を壊さずスキップします。

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

### 4.4 Fabric Data Agent / Fabric SQL

Agent1 のデモ既定は `FABRIC_DATA_AGENT_RUNTIME=sql` です。Fabric Data Agent の `aiassistant/openai`
Published URL は preview の thread / active run 再利用で不安定になる場合があるため、Web UI では
同じ Lakehouse の `FABRIC_SQL_ENDPOINT` を primary として使い、決定的な集計結果を返します。

Fabric Data Agent REST 経路を実験的に有効化する場合だけ `FABRIC_DATA_AGENT_RUNTIME=rest` を設定します:

```bash
az containerapp update --name <app> --resource-group <rg> \
  --set-env-vars FABRIC_DATA_AGENT_RUNTIME=rest FABRIC_DATA_AGENT_URL=https://api.fabric.microsoft.com/v1/workspaces/<ws>/dataagents/<da>/aiassistant/openai
```

`FABRIC_SQL_ENDPOINT` が利用不可の場合は CSV にフォールバックします。
GitHub Actions の Deploy workflow は production environment / repository variables の
`FABRIC_DATA_AGENT_URL`, `FABRIC_DATA_AGENT_RUNTIME`, `FABRIC_SQL_ENDPOINT`, `FABRIC_LAKEHOUSE_DATABASE`, `FABRIC_SALES_TABLE`, `FABRIC_REVIEWS_TABLE` を Container App に同期します。

#### 4.4.1 デモ向け Data Agent チューニング

`ws-3iq-demo` の `Travel_Ontology_DA` は、Fabric item definition API で **Agent instructions** と `travelIQ` ontology datasource instructions を直接更新できます。`POST /v1/workspaces/<workspace-id>/dataAgents/<data-agent-id>/getDefinition` で現在の definition を取得し、`Files/Config/draft/stage_config.json` / `Files/Config/published/stage_config.json` の `aiInstructions` と、`Files/Config/*/ontology-travelIQ/datasource.json` の `dataSourceInstructions` を更新してから、同じ definition payload を `POST /v1/workspaces/<workspace-id>/dataAgents/<data-agent-id>/updateDefinition` に送ってください。どちらも `202 Accepted` の非同期操作なので `Location` を poll し、getDefinition は `<operation-url>/result` で結果を取得します。

現行の source は `travelIQ` ontology（ID `c5c4b957-a9e3-4eb6-a7b6-738a4c27d57d`）、Lakehouse は `Travel_LH` です。instructions は特定プロンプトや固定デモ値ではなく、業務語、指標定義、テーブル選択、条件緩和、レビュー分析、未知列の代替提案、GQL/JSON 非表示を汎用ルールとしてまとめてください。

**Data agent instructions 推奨文:**

```text
あなたは Travel Marketing AI デモ用の Microsoft Fabric Data Agent です。目的は、旅行販売データとレビュー分析データを使って、マーケティング担当者が自然言語で売上傾向、顧客セグメント、旅行先、季節性、レビュー評価、感情傾向、改善示唆を分析できるようにすることです。回答は日本語で、実データに基づく数値・表・短い示唆を返してください。

# 利用可能なデータ
Source ontology: travelIQ
Lakehouse: Travel_LH

利用できるテーブルは以下のみです。存在しない列を作ったり、外部データを参照したりしないでください。

1. travel_sales
- Transaction_ID: 取引ID。travel_review との結合キー。
- Date: 予約・販売日または旅行関連日付として扱う。文字列の場合も日付として解釈できる範囲で使う。
- Travel_destination: 旅行先、目的地、観光地、地域、エリア。
- Category: 旅行カテゴリ、顧客カテゴリ、旅行タイプ、セグメント。
- Schedule: 旅行スケジュール、日程、プラン、旅行期間。
- Price: 取引単位の総額。売上、販売額、収益、旅行代金に使う。
- Price_per_person: 1人あたり単価。客単価、一人単価に使う。
- Number_of_people: 人数。旅行者数、参加人数、同行人数に使う。
- Age_group: 年齢層、年代、顧客年齢セグメント。

2. travel_review
- Transaction_ID: 取引ID。travel_sales との結合キー。
- Travel_destination: レビュー対象の旅行先、目的地。
- Rating: レビュー評価点。平均評価、高評価、低評価、評価分布に使う。
- Emotions: レビュー感情、感情ラベル、満足/不満傾向に使う。
- Comments: レビューコメント本文。顧客の声、要望、不満、良い点の把握に使う。

# テーブル選択ルール
- 売上、販売額、収益、予約数、取引数、人数、単価、旅行先別売上、カテゴリ別売上、年代別売上、季節別売上は travel_sales を使う。
- レビュー、口コミ、評価、Rating、感情、Emotions、コメント、満足、不満、評判、体験談、顧客の声は travel_review を使う。
- 売上と評価の関係、高評価旅行先の売上、カテゴリ別レビュー評価、年代別レビュー傾向など、販売情報とレビュー情報の両方が必要な質問は Transaction_ID で travel_sales と travel_review を結合する。
- review-only の質問では必ず travel_review を使う。travel_review の Rating、Emotions、Comments、Travel_destination で回答できる場合に「データがありません」と言わない。

# 主要な指標定義
- 売上 / 販売額 / 収益 / revenue / sales amount: SUM(travel_sales.Price)
- 予約数 / 取引数 / 件数 / bookings / transactions: COUNT(DISTINCT travel_sales.Transaction_ID)
- 旅行者数 / 参加人数 / people / travelers: SUM(travel_sales.Number_of_people)
- 平均取引額 / 平均旅行代金 / AOV: AVG(travel_sales.Price)
- 1人あたり平均単価 / 客単価 / per-person price: AVG(travel_sales.Price_per_person)
- 平均評価 / review score / rating: AVG(travel_review.Rating)
- レビュー件数 / 口コミ件数: COUNT(*) from travel_review
- 感情分布 / emotion mix: Emotions ごとの COUNT(*) と構成比
- 高評価: 原則 Rating >= 4
- 低評価: 原則 Rating <= 2
- 中立評価: 原則 Rating = 3

# 同義語・表現ゆれ
- Travel_destination: 旅行先、目的地、観光地、行き先、地域、エリア、destination。沖縄 / Okinawa、北海道 / Hokkaido、東京 / Tokyo など日本語・英語・大文字小文字・部分一致を考慮する。
- Category: カテゴリ、旅行タイプ、顧客タイプ、セグメント、家族、ファミリー、family、カップル、couple、一人旅、solo、友人、friends。
- Schedule: スケジュール、日程、プラン、旅程、旅行期間。
- Date: 日付、時期、月、年、四半期、季節、春、夏、秋、冬、ゴールデンウィーク、年末年始。
- Price: 売上、販売額、収益、旅行代金、購入金額、売上高、revenue、sales。
- Price_per_person: 1人あたり、客単価、一人単価、per person、unit price。
- Number_of_people: 人数、参加人数、旅行者数、同行人数、グループ人数、people、travelers。
- Age_group: 年代、年齢層、年齢セグメント、age group。
- Rating: 評価、星、スコア、満足度、rating、review score。
- Emotions: 感情、気持ち、ポジティブ、ネガティブ、満足、不満、emotion、sentiment。
- Comments: コメント、口コミ、レビュー本文、顧客の声、意見、要望、不満点、良かった点。

# 日付・季節
- 春: 3月、4月、5月。春休み: 3月、4月。
- 夏: 6月、7月、8月。秋: 9月、10月、11月。冬: 12月、1月、2月。
- ゴールデンウィーク: 原則 4月29日から5月5日。年が不明なら 4月下旬から5月上旬として扱い、仮定を明示する。
- 年末年始: 原則 12月下旬から1月上旬として扱い、仮定を明示する。
- 年を指定されていない場合は、利用可能な全期間で集計し、「年指定がないため、利用可能な全期間で集計しました」と書く。
- Category を季節判定に使わない。季節は Date の月だけから判断する。

# セグメント解釈
- 「ファミリー」「家族」「子連れ」「family」は Category の Family 表記ゆれを優先する。必要に応じて Number_of_people >= 3、Age_group が 30代/40代、Comments に「子連れ」「子ども」「家族」を含むレビューも近接条件として扱う。
- 「学生」は Age_group が 20代、または若年グループ旅行として Number_of_people >= 2 を優先する。
- 「若年層」は Age_group が 20代/30代、「シニア」は 50代以上として扱う。

# 条件緩和と不足時の扱い
- まずユーザー指定条件を厳密に適用する。
- 0件または極端に少ない場合は、表記ゆれ、全年度の同月、関連する Category/Schedule、近い年齢層の順に安全に広げる。
- 条件を広げた場合は、どの条件をどう広げたかを回答に含める。
- それでも回答できない場合は、できない理由と代替可能な分析を短く提案する。
- 広告費、利益、原価、天気、Web流入、キャンペーン名は現在のスキーマにない。聞かれた場合は、売上・予約数・人数・旅行先・カテゴリ・レビュー評価を使った代替分析を提案する。

# レビュー分析
- review-only の質問では travel_review を使い、平均評価、レビュー件数、Rating 分布、Emotions 分布、代表的な Comments を返す。
- Comments の要約は実データに存在するコメント内容だけを根拠にする。存在しない顧客の声、良い点、不満点を創作しない。
- 個別コメントを出す場合は最大5件程度に絞る。
- Rating と Emotions が矛盾して見える場合は両方を併記し、「評価点と感情ラベルに差がある可能性があります」と説明する。
- レビュー件数と Rating 分布など、同じ母集団の件数は合計が一致するように確認する。一致しない場合は不確かな分布を出さず、制約として説明する。

# 出力形式
原則として以下の順で回答する。
1. 結論: ユーザーの質問に対する短い答え。
2. 使用条件: 旅行先、カテゴリ、期間、年齢層、条件緩和の有無。
3. 主要指標: 売上、件数、人数、平均単価、平均評価、レビュー件数など relevant な指標。
4. 表: 比較が必要な場合は上位/下位、カテゴリ別、月別、旅行先別などの表を出す。
5. 補足: データ上の制約、広げた条件、解釈の仮定、次に見るべき観点。

- 表は原則25行以内。ランキングは指定がなければ上位5件または上位10件。
- 金額は円表記で、合計、平均、1人あたりを分ける。
- 比率を出す場合は分母を説明する。
- SQLやクエリを表示する場合は、最後に「参考SQL」として簡潔に示す。
- 内部の GQL、GraphQL、JSON、クエリ実行トレース、ツール呼び出し詳細は出力しない。マーケティング担当者向けの分析結果だけを出力する。
- X/XX/XXX、架空の例、プレースホルダー値は絶対に使わない。実データがない項目は「データなし」と明記する。
- 全件データの出力、書き込み、更新、削除、テーブル作成、外部送信は行わない。読み取り分析のみを行う。
```

**Publish 前の validation prompts:**

| 質問例 | 期待する動き |
| --- | --- |
| 春の沖縄ファミリー向け施策を分析して | travel_sales を使い、季節を Date の月で判定し、売上・予約数・人数・平均単価を返す |
| 沖縄のレビュー評価と代表コメントを教えて | travel_review だけで回答し、平均評価・レビュー件数・感情分布・実在コメントを返す |
| 若年層に人気の旅行先を売上順に出して | Age_group 20代/30代を使い、Travel_destination 別に売上と件数を集計する |
| カテゴリ別に売上とレビュー満足度を比較して | Transaction_ID で join し、Category 別の売上・件数・平均評価を返す |
| 広告費に対する ROI を旅行先別に教えて | 広告費/ROI 列がないことを説明し、売上・予約数・レビュー評価での代替分析を提案する |

Publish 前に draft chat で上記を確認し、特に review-only の質問が `travel_review` を使って回答できること、架空値やプレースホルダーを出さないこと、条件緩和時に仮定を明示することを確認してください。

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
4. `foundry_tool` を使うため、同じ SPA app registration から **Foundry data-plane delegated permission** を取得できるようにする
   - browser/MSAL が要求する scope は `https://ai.azure.com/user_impersonation`
   - これは FastAPI が Foundry Responses API を **ユーザーとして** 呼ぶための token で、Graph token の代替ではない
   - Work IQ / Microsoft 365 Copilot connector 側の tenant-wide enablement は別途必要だが、SPA が Agent 365 Tools scope を直接要求する必要はない
5. SPA redirect URI にはアプリ本体 URL に加えて **専用 redirect bridge ページ** も登録する
   - `http://localhost:5173/auth-redirect.html`
   - `http://localhost:8000/auth-redirect.html`
   - `https://<container-app-host>/auth-redirect.html`
6. 既定 runtime は **`MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`**。`postprovision.py` が Agent2 用の Foundry Prompt Agent を同期し、実行時はその `agent_reference` を使う。instructions を更新した場合も、反映には `scripts/postprovision.py` の再実行で marketing-plan agent の再同期が必要
7. `WORKIQ_RUNTIME=foundry_tool` では、事前作成済み Agent に対して read-only の Microsoft 365 connector を使う。frontend は `https://ai.azure.com/user_impersonation` を取得し、backend はその token で Foundry Responses API をユーザーとして呼ぶ。`meeting_notes` は Teams、`emails` は Outlook Email、`teams_chats` は Teams、`documents_notes` は SharePoint を使う。追加の Work IQ endpoint 環境変数は不要
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

現行の移行先 workspace は `ws-3iq-demo` です。Fabric 側で Lakehouse / Data Agent を再作成したら、Published URL と SQL endpoint を GitHub Actions variables または Container App env に反映してください。

以前の `workiq-dev` tenant では次の構成を使っていました。

- capacity: `fcdemojapaneast001`
- workspace: `ws-MG-pod2`
- lakehouse: `Travel_Lakehouse`
- tables: `sales_results`, `customer_reviews`
- data agent: `FFA_DataAgent`

別 tenant で再現する場合:

1. Fabric workspace を作成または復旧する
2. Lakehouse と SQL endpoint を作成し、`sales_results` / `customer_reviews` を投入する
3. 必要に応じて Fabric Data Agent を publish する
4. Container App または GitHub Actions variables に `FABRIC_SQL_ENDPOINT` / `FABRIC_DATA_AGENT_URL` / `FABRIC_LAKEHOUSE_DATABASE` / `FABRIC_SALES_TABLE` / `FABRIC_REVIEWS_TABLE` を設定する

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

### 4.9 Source ingestion / MAI Transcribe / monitoring gates

これらは rollout 用の default-off 機能であり、`azd up` だけでは本番有効化されません。

| 機能 | 有効化条件 | 検証 |
| --- | --- | --- |
| Capabilities | 追加設定不要 | `curl https://<app>/api/capabilities` が endpoint / connection string を含まず `available` / `configured` だけを返す |
| Source ingestion | `ENABLE_SOURCE_INGESTION=true` | `GET /api/sources/limits` が `enabled=true` を返し、`POST /api/sources/text` が `pending_review` source を返す。default-off では `SOURCE_INGESTION_DISABLED` |
| PDF source | Source ingestion + 任意で `CONTENT_UNDERSTANDING_ENDPOINT` | `POST /api/sources/pdf` が PDF magic と byte 上限を検証し、解析不可でも raw text なしの draft を返す |
| Audio source | Source ingestion + `ENABLE_MAI_TRANSCRIBE_1=true` + `MAI_TRANSCRIBE_1_ENDPOINT` + `MAI_TRANSCRIBE_1_DEPLOYMENT_NAME` + `MAI_TRANSCRIBE_1_API_PATH` | 未設定では `AUDIO_TRANSCRIBE_UNAVAILABLE`。raw audio は保存せず、短命 HTTPS `audio_url` の transcript だけを draft にする |
| Evaluation logging | `ENABLE_EVALUATION_LOGGING=true` + project endpoint | raw prompt / Work IQ content / transcript / bearer token / brochure HTML を含まない最小 payload だけを Foundry へ送る |
| Continuous monitoring | Evaluation logging + `ENABLE_CONTINUOUS_MONITORING=true` + sample rate > 0 | App Insights custom metrics / Foundry logging が sampled async で送られ、API 応答をブロックしない |
| Cost metrics | `ENABLE_COST_METRICS=true` + App Insights | `done.metrics.estimated_cost_usd` は token usage からの概算。請求確定値ではない |

owner-scoped API は `REQUIRE_AUTHENTICATED_OWNER=true` のときだけ認証済み owner boundary を要求します。Bearer claims は署名検証済み upstream auth/proxy がある場合だけ `TRUST_AUTH_HEADER_CLAIMS` または trusted header で信頼してください。

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
curl https://<app>/api/capabilities
curl https://<app>/api/sources/limits
```

| 確認項目 | 期待動作 |
| --- | --- |
| ナレッジベース | `search_knowledge_base()` が検索結果を返す (静的レスポンスでない) |
| 画像生成 | GPT Image 1.5 と MAI 経路の両方でヒーロー画像が透明 PNG でない |
| 動画生成 | MP4 が返る (SSML ナレーション付き) |
| Voice Live | `/api/voice-config` が MSAL 設定を返す |
| Capabilities | `/api/capabilities` が endpoint / connection string を返さず boolean feature 状態だけを返す |
| Source ingestion | default-off では `SOURCE_INGESTION_DISABLED`、有効化後は text/PDF/audio source がレビュー待ち draft になる |
| Fabric | Agent1 が Fabric Data Agent または Fabric SQL endpoint を使い、CSV フォールバックにならない |
| 承認後 Teams 通知 | `logic-wmbvhdhcsuyb2` の run history が success で、対象 Team / channel に投稿される |
| 評価 | `/api/evaluate` が `builtin`、`plan_quality`、`asset_quality`、`regression_guard` を返す |
| 継続監視 | 評価ログ opt-in + sample rate 条件を満たす場合だけ最小 payload が非同期送信される |

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
| `/api/sources/*` が 503 | `ENABLE_SOURCE_INGESTION=true` が Container App に反映済みか確認。音声だけ失敗する場合は MAI Transcribe の endpoint / deployment / API path を確認 |
| `/api/capabilities` で `configured=true` だが `available=false` | 必須 endpoint、App Insights、sample rate、deployment/quota、または privacy gate が不足していないか確認 |
| 上司通知が飛ばない | `MANAGER_APPROVAL_TRIGGER_URL` を確認し、Container App secret に `?api-version=...&sp=...&sv=...&sig=...` を含む full signed URL が入っているか確かめる。`deploy.yml` の signed URL 再同期が成功しているかも確認する。未設定でも承認ページ自体は動作 |
| 承認後 Teams 通知が飛ばない | `LOGIC_APP_CALLBACK_URL`、`logic-wmbvhdhcsuyb2` の run history、Teams connection `teams-1`、Team / channel ID を確認 |
| SharePoint へ保存されない | target site への permission grant（preferred）または `sharepointonline` connector の認証状態を確認 |
