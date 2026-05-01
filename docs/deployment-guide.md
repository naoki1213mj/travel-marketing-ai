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
- Marketing-plan Prompt Agent の作成 / 再同期
- Entra SPA アプリ登録 (Voice Live + Work IQ delegated auth 用)

### postprovision 後の手動作業

- 現在の rebuilt `workiq-dev` tenant では **Search/KB**, **Work IQ admin consent**, **gpt-4.1 / gpt-5.4 deployments**, **別 East US MAI endpoint**, **Fabric rebuild**, **manager approval workflow**, **post-approval Teams channel notification** までは完了済みです
- 新しい tenant を一から立ち上げる場合は、以下の項目が引き続き手動です
- Azure AI Search の作成と `regulations-index` の投入
- Foundry → AI Search 接続の追加
- `FABRIC_DATA_AGENT_URL` / `SPEECH_SERVICE_ENDPOINT` 等の設定
- Work IQ 用 SPA app registration の Graph delegated permissions 追加 + admin consent
- Fabric Lakehouse / SQL endpoint / Fabric Data Agent の新テナント側再作成
- SharePoint 保存経路の復旧（preferred: site permission grant to Logic App MI、fallback: SharePoint connector 再認証）
- Logic Apps の Teams / SharePoint connector や trigger URL が変わる場合の再接続 / 再設定

Work IQ は既定で **`MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`** を使います。`postprovision.py` が Agent2 用の事前作成済み Foundry Prompt Agent を同期し、実行時はその `agent_reference` を **Foundry delegated token (`https://ai.azure.com/user_impersonation`)** 付きの Responses API で呼び出します。Prompt Agent 側の instructions は、添付済みの Work IQ / Microsoft 365 tools を優先利用する前提で同期されます。postprovision は **UI で選択できる text model 全件** (`gpt-5-4-mini`, `gpt-5.4`, `gpt-4-1-mini`, `gpt-4.1`) をまとめて同期します。**`graph_prefetch` は明示 rollback** で、Microsoft Graph Copilot Chat API を per-user delegated token で呼び出して短い brief を先読みします。必要なのは SPA app registration の権限/consent であり、追加の Work IQ API endpoint 環境変数はありません。instructions を変えた場合は marketing-plan agent を再同期してください。

詳細は [azure-setup.md](azure-setup.md) を参照してください。

### Current production snapshot (`workiq-dev`, 2026-05-01 cutover complete)

| Area | State |
| --- | --- |
| Search / Foundry IQ | Azure AI Search was created in **East US** (East US 2 had no capacity), and `regulations-index`, `regulations-ks`, and `regulations-kb` are already wired into the Container App |
| Work IQ | SPA redirect URIs, Graph delegated permissions, tenant-wide admin consent, and Microsoft 365 Copilot license verification are complete |
| Work IQ runtime | The default runtime is `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`. Agent2 uses the pre-provisioned Foundry Prompt Agent through `agent_reference`; the frontend acquires `https://ai.azure.com/user_impersonation`, and the backend passes that delegated token to the Foundry Responses client so the attached Work IQ MCP connection runs per-user. `source_scope` is guidance/metadata, not a dynamic connector overlay. `graph_prefetch` remains the explicit rollback path, where a short Graph Copilot Chat API brief is prefetched (`chatOverStream` preferred, `/chat` fallback, `WORK_IQ_TIMEOUT_SECONDS=120`). Frontend preflight surfaces `auth_required`, `consent_required`, and `redirecting`, and the backend persists `work_iq_session` status so restored conversations keep the same Work IQ UI state. Accounts outside the tenant/guest list are rejected during sign-in |
| Text models | `gpt-5-4-mini`, `gpt-4-1-mini`, `gpt-4.1`, and `gpt-5.4` exist on the main East US 2 Foundry account. `gpt-5.5` is visible in the East US 2 catalog as GA (`2026-04-24`, Responses-capable), but this subscription currently has 0 TPM quota for it; request quota and deploy it before selecting it in the UI. The app default image route is `gpt-image-2`; GPT image calls use the Azure OpenAI Images API against the account endpoint derived from `AZURE_AI_PROJECT_ENDPOINT`, so deploy it under the default name or set `GPT_IMAGE_2_DEPLOYMENT_NAME` when the deployment name differs. `gpt-image-1.5` remains supported |
| MAI image route | A separate East US AI Services account is wired through `IMAGE_PROJECT_ENDPOINT_MAI`; the live `MAI-Image-2` deployment name currently points to the `MAI-Image-2e` model because direct `MAI-Image-2` quota wasn't available |
| Fabric | Fabric capacity `fcdemoeastus2001` (East US 2, F64, **Active**) backs workspace `ws-3iq-demo`. The Phase 9 v2 lakehouse `lh_travel_marketing_v2` (10 Delta tables in `dbo` schema) is the live data source. Data Agent v2 `Travel_Ontology_DA_v2` (`b85b67a4-bac4-4852-95e1-443c02032844`) is published with the Phase 10 `displayNamePropertyId` enrichment + tuned aiInstructions (best-of grade A 12/14). `FABRIC_DATA_AGENT_URL_V2`, `FABRIC_DATA_AGENT_RUNTIME_VERSION=v2`, and the v2 SQL endpoint are wired into the Container App. The legacy `Travel_LH` lakehouse is retained as v1 rollback only |
| Logic Apps / Teams | `teams-1` is Connected, `logic-manager-approval-wmbvhdhcsuyb2` is live, and `logic-wmbvhdhcsuyb2` can post the post-approval message to the target Teams channel. The signed manager trigger URL sync in `deploy.yml` has also been revalidated against the live Container App secret |
| Container Apps VNet integration | **Cutover complete (2026-05-01)**. The new VNet-integrated CAE `cae-wmbvhdhcsuyb2-pn` (default domain `wonderfultree-f9803f6f.eastus2.azurecontainerapps.io`) and Container App `ca-wmbvhdhcsuyb2-pn` are the only live environment in `snet-container-apps`; both connect to Cosmos DB and Key Vault private endpoints over the VNet (`publicNetworkAccess` stays `Disabled`). The pre-migration `cae-wmbvhdhcsuyb2` / `ca-wmbvhdhcsuyb2` resources were deleted on 2026-05-01 after stability verification |
| Approval security | `/api/chat/{id}/approve` is bound to a per-conversation `approval_token` (32-byte urlsafe) that `chat()` mints after Agent2 succeeds and emits in the `approval_request` SSE event. Anonymous external `/approve` requests must echo the token; missing / mismatched tokens return `APPROVAL_CONTEXT_NOT_FOUND`. The token rotates per `_refine_events()` revision, is stored in Cosmos `metadata.pending_approval_token` while in `awaiting_approval` / `awaiting_manager_approval`, and is constant-time compared via `hmac.compare_digest`. Authenticated users (Entra Bearer) keep working on owner_id match alone. See [`approval-security.md`](approval-security.md) for the full security model |
| Remaining manual work | Finish the SharePoint save path by granting the target site permission to the post-approval Logic App managed identity, or re-authenticate the SharePoint connector as a fallback. Microsoft Fabric P13 / P14 prompts (`円安後の海外売上回復の度合い` / `インバウンド比率の四半期推移`) hit a Fabric platform-side `submit_tool_outputs` BadRequest and need a Microsoft support escalation; Phase 10 `aiInstructions` cannot fix it |

### Container Apps VNet integration migration runbook (historical reference)

The cutover from the legacy non-VNet-integrated CAE to the current `-pn` CAE is **complete** as of 2026-05-01. The runbook below is preserved for the next time this kind of side-by-side rebuild is required, since Azure does not allow `vnetConfiguration` to be added to an existing CAE and `managedEnvironmentId` is immutable on the Container App.

```bash
azd env set ENABLE_CONTAINER_APPS_VNET_INTEGRATION true
azd env set CONTAINER_APPS_VNET_INTEGRATION_MIGRATION_APPROVAL CONFIRM_CAE_VNET_MIGRATION
azd provision
```

When both flags are set, `infra/main.bicep` appends a `-pn` suffix to the CAE and Container App names (e.g. `cae-<token>-pn`, `ca-<token>-pn`) and creates the new resources alongside the originals. The original CAE / Container App are no longer in Bicep, so `azd provision` leaves them untouched. After the new Container App becomes Healthy, `azd deploy` and the `azure.yaml` `web` service automatically target the new FQDN through the updated `AZURE_CONTAINER_APP_NAME` and `SERVICE_WEB_ENDPOINTS` outputs.

Operational gotchas (verified during the 2026-04-30 cutover):

1. **AcrPull race**: The new Container App's initial revision pulls the image from ACR before the AcrPull / Key Vault Secrets User role assignments emitted by the same Bicep module finish propagating. The first `azd provision` may fail with `Operation expired` on revision creation. Re-run `azd provision`; the second pass succeeds because the role assignments now exist.
2. **Manual role-assignment cleanup**: If you create AcrPull manually with `az role assignment create`, delete it before re-running `azd provision`. Bicep's deterministic `guid()`-based assignment IDs differ from manual ones, leading to `RoleAssignmentExists` errors on the next pass.
3. **SPA Redirect URIs**: `scripts/postprovision.py::_ensure_spa_redirect_uris` merges old and new FQDNs into the `travel-voice-spa` Entra app, so MSAL sign-in keeps working on both URLs throughout the migration.
4. **Connectivity smoke test from inside the new container**:
   ```bash
   az containerapp exec -n ca-<token>-pn -g <rg> --command "getent hosts cosmos-<token>.documents.azure.com"
   # → 10.0.x.x  cosmos-<token>.privatelink.documents.azure.com  cosmos-<token>.documents.azure.com
   ```
   Then validate `https://<new-fqdn>/api/health` returns `200 {"status":"ok"}` and `/api/ready` returns `{"status":"ready","missing":[]}`.
5. **Drain the old environment** only after the new FQDN has run cleanly for 1–2 hours:
   ```bash
   az containerapp delete -n ca-<token> -g <rg> --yes
   az containerapp env delete -n cae-<token> -g <rg> --yes
   ```

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
| `ENABLE_MODEL_ROUTER` | 任意 | Model Router を UI / capabilities に公開する場合だけ `true` |
| `MODEL_ROUTER_ENDPOINT` | 任意 | Model Router endpoint。`/api/capabilities` には値を返さない |
| `MODEL_ROUTER_DEPLOYMENT_NAME` | 任意 | Model Router deployment 名 |
| `ENABLE_GPT_55` | 任意 | gpt-5.5 を UI / capabilities に公開する場合だけ `true`。deployment/quota 作成後に有効化 |
| `GPT_55_DEPLOYMENT_NAME` | 任意 | gpt-5.5 deployment 名を既定から変える場合 |
| `EVAL_MODEL_DEPLOYMENT` | 推奨 | 評価用の専用 deployment |
| `ENABLE_COST_METRICS` | 任意 | token usage から概算コストを表示する場合だけ `true`。請求データではない |
| `ENABLE_FOUNDRY_TRACING` | 任意 | Foundry/App Insights tracing を opt-in する場合だけ `true` |
| `ENABLE_EVALUATION_LOGGING` | 任意 | Foundry への評価ログ送信を opt-in する場合だけ `true`。raw prompt / Work IQ content / transcript / bearer token / brochure HTML は送信しない |
| `ENABLE_CONTINUOUS_MONITORING` | 任意 | 評価ログ opt-in 時だけ有効な継続監視。SSE/API 応答後にサンプル済みの最小 payload を非同期送信 |
| `CONTINUOUS_MONITORING_SAMPLE_RATE` | 任意 | 継続監視の決定的サンプリング率（0.0〜1.0、既定 0.1） |
| `EVALUATION_LOG_RETENTION_DAYS` | 任意 | Foundry 評価ログの運用保持目安（日、既定 30）。project 側の保持/削除運用と合わせて管理 |
| `COSMOS_DB_ENDPOINT` | 任意 | 会話履歴保存 |
| `SEARCH_ENDPOINT` | 任意 | Azure AI Search endpoint (`search_knowledge_base()` はこれを最優先で使う) |
| `SEARCH_API_KEY` | 任意 | Azure AI Search 管理キー。live tenant では Container Apps secret で保持 |
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent Published URL (`https://api.fabric.microsoft.com/v1/workspaces/<workspace-id>/dataagents/<data-agent-id>/aiassistant/openai`) |
| `FABRIC_SQL_ENDPOINT` | 任意 | Fabric SQL フォールバック |
| `FABRIC_LAKEHOUSE_DATABASE` | 任意 | Fabric SQL フォールバック時の Lakehouse database 名。未設定時は `lh_travel_marketing_v2`（live 環境の v2 既定） |
| `FABRIC_SALES_TABLE` | 任意 | Fabric SQL フォールバック時の販売テーブル名。未設定時は `sales_results` |
| `FABRIC_REVIEWS_TABLE` | 任意 | Fabric SQL フォールバック時のレビューテーブル名。未設定時は `customer_reviews` |
| `IMPROVEMENT_MCP_ENDPOINT` | 任意 | APIM MCP ルート |
| `MCP_REGISTRY_ENDPOINT` | 任意 | MCP registry UI / capabilities 用 endpoint |
| `MARKETING_PLAN_RUNTIME` | 任意 | marketing-plan runtime（既定: `foundry_preprovisioned`） |
| `WORKIQ_RUNTIME` | 任意 | Work IQ runtime（既定: `foundry_tool`）。`graph_prefetch` は明示 rollback 用 |
| `WORK_IQ_TIMEOUT_SECONDS` | 任意 | Graph Copilot Chat API 取得 timeout（秒、既定 120） |
| `PUBLIC_APP_BASE_URL` | 上司承認で推奨 | manager approval / callback URL に使う canonical public URL |
| `ENABLE_GITHUB_COPILOT_REVIEW_AGENT` | 任意 | preview の `GitHubCopilotAgent` 品質レビューを opt-in するときだけ `true` |
| `IMAGE_PROJECT_ENDPOINT_MAI` | 任意 | 別の MAI 対応 AI Services endpoint |
| `ENABLE_SOURCE_INGESTION` | 任意 | ユーザー提供ソース取り込み API。accidental production-on 防止のため既定 `false` |
| `SOURCE_INGESTION_ENDPOINT` | 任意 | 外部 ingestion service を使う場合の endpoint。ローカル `/api/sources/*` には不要 |
| `SOURCE_MAX_ITEMS_PER_OWNER` | 任意 | owner ごとの保存 source 数上限（既定 20、最大 100） |
| `SOURCE_TTL_SECONDS` | 任意 | source draft の TTL 秒数（既定 604800、最大 30 日） |
| `SOURCE_MAX_TEXT_CHARS` | 任意 | text / transcript / PDF 抽出テキストの最大文字数（既定 20000、最大 50000） |
| `SOURCE_MAX_PDF_BYTES` | 任意 | PDF upload の最大 byte 数（既定 10MiB、最大 25MiB） |
| `SOURCE_MAX_AUDIO_SECONDS` | 任意 | audio_url の想定音声長上限（既定 1800 秒、最大 3600 秒） |
| `SOURCE_MAX_AUDIO_BYTES` | 任意 | audio_url の想定音声サイズ上限（既定 25MiB、最大 100MiB） |
| `ENABLE_MAI_TRANSCRIBE_1` | 任意 | MAI-Transcribe-1 音声 source ingestion を opt-in する場合だけ `true` |
| `MAI_TRANSCRIBE_1_ENDPOINT` | 任意 | REST contract 確認済みの MAI Transcribe endpoint |
| `MAI_TRANSCRIBE_1_DEPLOYMENT_NAME` | 任意 | MAI-Transcribe-1 deployment 名 |
| `MAI_TRANSCRIBE_1_API_PATH` | 任意 | 確認済みの Transcribe API path。未設定時は呼び出さない |
| `SPEECH_SERVICE_ENDPOINT` | 任意 | Photo Avatar 動画生成 |
| `SPEECH_SERVICE_REGION` | 任意 | Speech リージョン |
| `ENABLE_VOICE_TALK_TO_START` | 任意 | Voice Live の talk-to-start UX を公開する場合だけ `true` |
| `LOGIC_APP_CALLBACK_URL` | 任意 | 承認後アクション workflow。signed URL なので secret として扱う |
| `MANAGER_APPROVAL_TRIGGER_URL` | 任意 | 上司承認通知 workflow。signed URL なので secret として扱う |
| `TRUST_AUTH_HEADER_CLAIMS` | 任意 | 署名検証済み upstream auth がある場合だけ bearer claims を信頼する。通常は `false` |
| `TRUSTED_AUTH_HEADER_NAME` / `TRUSTED_AUTH_HEADER_VALUE` | 任意 | upstream が検証済み request に付ける境界ヘッダー |
| `REQUIRE_AUTHENTICATED_OWNER` | 任意 | owner-scoped API に認証を強制する場合だけ `true`。未設定なら本番相当環境でも Work IQ off の通常チャットは匿名 owner で開始可能 |
| `SERVE_STATIC` | 任意 | コンテナ内フロントエンド配信 (`true`) |
| `API_KEY` | 任意 | API エンドポイント保護 |

全項目は [.env.example](../.env.example) を参照してください。

### ロールアウト gate

- `/api/capabilities` は feature flag / 接続状態を `available` / `configured` の boolean だけで返し、endpoint、connection string、tenant 固有値は返しません。UI 表示可否はこの endpoint を優先してください。
- Source ingestion は `ENABLE_SOURCE_INGESTION=true` を明示した環境だけで有効です。raw audio は保存・返却せず、API は短命 `audio_url` を transcribe adapter に渡すだけです。text/PDF draft は owner scope の get/review/delete と TTL cleanup の対象で、公開 payload に raw text は含めません。`GET /api/sources/limits` で secret を含まない有効状態と運用上限を確認できます。
- MAI Transcribe は `ENABLE_MAI_TRANSCRIBE_1=true` に加えて endpoint / deployment / 確認済み API path が揃うまで unavailable です。未確認の REST path を推測して呼びません。
- 評価ログと継続監視は privacy gate です。`ENABLE_EVALUATION_LOGGING=true` なしでは Foundry へ送信せず、`ENABLE_CONTINUOUS_MONITORING=true` だけでは有効になりません。送信 payload は raw prompt / Work IQ content / transcript / bearer token / brochure HTML を含めない最小化済みデータです。
- `ENABLE_COST_METRICS=true` で表示される cost は token usage からの推定であり、Azure Cost Management の課金確定値ではありません。
- Work IQ の `foundry_tool` 経路は既定ですが、ユーザーの `https://ai.azure.com/user_impersonation` token と tenant consent がない場合は fail-closed です。`graph_prefetch` は明示 rollback のみです。
- owner-scoped API（会話、source ingestion）は `REQUIRE_AUTHENTICATED_OWNER=true` のときだけ認証済み owner boundary を要求します。Bearer claim は信頼済み upstream 境界がある場合だけ使ってください。

> Logic App の signed trigger URL は `&sp=...&sv=...&sig=...` を含みます。Container App secret や `azd env` へ反映するときは **URL 全体を 1 つの値として引用**し、途中で切れないようにしてください。
>
> `deploy.yml` は manager approval workflow の signed trigger URL を Azure から毎回引き直して Container App secret へ同期します。GitHub Actions 側で `MANAGER_APPROVAL_TRIGGER_URL` を別 secret として持つ必要はありません。この同期経路は live 環境でも再確認済みです。

## 7. デプロイ後の確認

```bash
curl https://<your-app>/api/health
curl https://<your-app>/api/ready
curl https://<your-app>/api/capabilities
curl https://<your-app>/api/sources/limits
```

`/api/ready` が `503` の場合、レスポンスの `missing` 配列に不足設定が表示されます。

## 8. CI/CD (GitHub Actions)

### CI (`ci.yml`)

Ruff lint → pytest → frontend lint → TypeScript check → frontend build

### Deploy (`deploy.yml`)

1. Azure OIDC ログイン
2. `az acr build`
3. `az containerapp update`
4. `/api/health` + `/api/ready` チェック

### Security (`security.yml`)

Trivy, Gitleaks, npm audit, pip-audit, bandit

## 9. トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| デモモードになる | `AZURE_AI_PROJECT_ENDPOINT` を設定 |
| `/api/ready` が `degraded` | `ENVIRONMENT=production` で必須変数が不足 |
| `gpt-4.1` / `gpt-5.4` が使えない | Azure 側の deployment 名が UI 値と一致しているか確認 (`gpt-4.1`, `gpt-4-1-mini`, `gpt-5.4`) |
| 画像が透明 PNG | `IMAGE_PROJECT_ENDPOINT_MAI` と別 East US MAI account の RBAC を確認。`MAI-Image-2` quota が無い subscription では `MAI-Image-2e` を `MAI-Image-2` deployment 名で alias すると現行 backend で利用可能 |
| MCP が使われない | `IMPROVEMENT_MCP_ENDPOINT` の APIM route を確認 |
| Work IQ が `timeout` / `completed` にならない | App Insights で Microsoft Graph Copilot Chat API `chatOverStream` / `/chat` のレイテンシを確認し、必要なら `WORK_IQ_TIMEOUT_SECONDS` を 120 以上へ調整する |
| `work_iq_runtime=foundry_tool` が失敗する | `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` になっているか、`postprovision.py` で marketing-plan Agent が同期済みか確認する。必要なら `WORKIQ_RUNTIME=graph_prefetch` に切り替えて切り分ける |
| Work IQ サインインで弾かれる | サインインに使っている Microsoft 365 アカウントが tenant member / guest か確認する。tenant 外アカウントは SPA redirect 後に拒否される |
| `/api/sources/*` が 503 | `ENABLE_SOURCE_INGESTION=true` が Container App に反映済みか確認。音声だけ失敗する場合は `ENABLE_MAI_TRANSCRIBE_1` と MAI Transcribe endpoint / deployment / API path を確認する |
| `/api/capabilities` で機能が `configured=true` でも `available=false` | feature flag だけでなく必須 endpoint、App Insights、sample rate、deployment/quota が揃っているか確認。既定では未完成機能を production-ready として公開しない |
| 上司承認通知が飛ばない | `logic-manager-approval-*` の run history と Container App secret `manager-approval-trigger-url` に `&sp=...&sv=...&sig=...` を含む full signed URL が入っているか確認。`deploy.yml` の signed URL 再同期が成功しているかも確認する。未設定でも承認ページ自体は動作 |
| 承認後 Teams 通知が飛ばない | `LOGIC_APP_CALLBACK_URL`、`logic-wmbvhdhcsuyb2` の run history、Teams connection `teams-1`、対象 Team / channel を確認 |
| SharePoint に保存されない | target site への permission grant か `sharepointonline` connector の認証状態を確認 |
| KB が静的レスポンス | `SEARCH_ENDPOINT` / `SEARCH_API_KEY` または Foundry の Azure AI Search 既定接続、`regulations-index` / `regulations-kb` を確認 |
