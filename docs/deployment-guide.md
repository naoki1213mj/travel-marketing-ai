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

Work IQ は既定で **`MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`** を使います。`postprovision.py` が Agent2 用の事前作成済み Foundry Prompt Agent を同期し、実行時はその `agent_reference` に `source_scope` ベースの read-only Microsoft 365 connector を per-user delegated token 付きで overlay します。Prompt Agent 側の instructions は、実行時に Work IQ / Microsoft 365 tools が付与されている場合はそれらを優先利用する前提で同期されます。**`graph_prefetch` は明示 rollback** で、Microsoft Graph Copilot Chat API を per-user delegated token で呼び出して短い brief を先読みします。必要なのは SPA app registration の権限/consent であり、追加の Work IQ API endpoint 環境変数はありません。instructions を変えた場合は marketing-plan agent を再同期してください。

詳細は [azure-setup.md](azure-setup.md) を参照してください。

### Current rebuilt-tenant snapshot (`workiq-dev`, 2026-04-18)

| Area | State |
| --- | --- |
| Search / Foundry IQ | Azure AI Search was created in **East US** (East US 2 had no capacity), and `regulations-index`, `regulations-ks`, and `regulations-kb` are already wired into the Container App |
| Work IQ | SPA redirect URIs, Graph delegated permissions, tenant-wide admin consent, and Microsoft 365 Copilot license verification are complete |
| Work IQ runtime | The default runtime is `MARKETING_PLAN_RUNTIME=foundry_preprovisioned` + `WORKIQ_RUNTIME=foundry_tool`. Agent2 uses a pre-provisioned Foundry agent and overlays read-only Microsoft 365 connectors from `source_scope` when a delegated token is present. `graph_prefetch` remains the explicit rollback path, where a short Graph Copilot Chat API brief is prefetched (`chatOverStream` preferred, `/chat` fallback, `WORK_IQ_TIMEOUT_SECONDS=120`). Frontend preflight surfaces `auth_required`, `consent_required`, and `redirecting`, and the backend persists `work_iq_session` status so restored conversations keep the same Work IQ UI state. Accounts outside the tenant/guest list are rejected during sign-in |
| Text models | `gpt-5-4-mini`, `gpt-4-1-mini`, `gpt-4.1`, `gpt-5.4`, and `gpt-image-1.5` exist on the main East US 2 Foundry account. If `gpt-image-2` is added under a custom deployment name, set `GPT_IMAGE_2_DEPLOYMENT_NAME` so the app selects the right deployment header |
| MAI image route | A separate East US AI Services account is wired through `IMAGE_PROJECT_ENDPOINT_MAI`; the live `MAI-Image-2` deployment name currently points to the `MAI-Image-2e` model because direct `MAI-Image-2` quota wasn't available |
| Fabric | Fabric capacity `fcdemojapaneast001`, workspace `ws-MG-pod2`, lakehouse `Travel_Lakehouse`, and the `sales_results` / `customer_reviews` tables are restored, and both `FABRIC_DATA_AGENT_URL` and `FABRIC_SQL_ENDPOINT` are wired into the Container App |
| Logic Apps / Teams | `teams-1` is Connected, `logic-manager-approval-wmbvhdhcsuyb2` is live, and `logic-wmbvhdhcsuyb2` can post the post-approval message to the target Teams channel. The signed manager trigger URL sync in `deploy.yml` has also been revalidated against the live Container App secret |
| Remaining manual work | Finish the SharePoint save path by granting the target site permission to the post-approval Logic App managed identity, or re-authenticate the SharePoint connector as a fallback |

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
| `SEARCH_ENDPOINT` | 任意 | Azure AI Search endpoint (`search_knowledge_base()` はこれを最優先で使う) |
| `SEARCH_API_KEY` | 任意 | Azure AI Search 管理キー。live tenant では Container Apps secret で保持 |
| `FABRIC_DATA_AGENT_URL` | 推奨 | Fabric Data Agent Published URL |
| `FABRIC_SQL_ENDPOINT` | 任意 | Fabric SQL フォールバック |
| `IMPROVEMENT_MCP_ENDPOINT` | 任意 | APIM MCP ルート |
| `MARKETING_PLAN_RUNTIME` | 任意 | marketing-plan runtime（既定: `foundry_preprovisioned`） |
| `WORKIQ_RUNTIME` | 任意 | Work IQ runtime（既定: `foundry_tool`）。`graph_prefetch` は明示 rollback 用 |
| `WORK_IQ_TIMEOUT_SECONDS` | 任意 | Graph Copilot Chat API 取得 timeout（秒、既定 120） |
| `IMAGE_PROJECT_ENDPOINT_MAI` | 任意 | 別の MAI 対応 AI Services endpoint |
| `SPEECH_SERVICE_ENDPOINT` | 任意 | Photo Avatar 動画生成 |
| `SPEECH_SERVICE_REGION` | 任意 | Speech リージョン |
| `LOGIC_APP_CALLBACK_URL` | 任意 | 承認後アクション workflow。signed URL なので secret として扱う |
| `MANAGER_APPROVAL_TRIGGER_URL` | 任意 | 上司承認通知 workflow。signed URL なので secret として扱う |
| `SERVE_STATIC` | 任意 | コンテナ内フロントエンド配信 (`true`) |
| `API_KEY` | 任意 | API エンドポイント保護 |

全項目は [.env.example](../.env.example) を参照してください。

> Logic App の signed trigger URL は `&sp=...&sv=...&sig=...` を含みます。Container App secret や `azd env` へ反映するときは **URL 全体を 1 つの値として引用**し、途中で切れないようにしてください。
>
> `deploy.yml` は manager approval workflow の signed trigger URL を Azure から毎回引き直して Container App secret へ同期します。GitHub Actions 側で `MANAGER_APPROVAL_TRIGGER_URL` を別 secret として持つ必要はありません。この同期経路は live 環境でも再確認済みです。

## 7. デプロイ後の確認

```bash
curl https://<your-app>/api/health
curl https://<your-app>/api/ready
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
| 上司承認通知が飛ばない | `logic-manager-approval-*` の run history と Container App secret `manager-approval-trigger-url` に `&sp=...&sv=...&sig=...` を含む full signed URL が入っているか確認。`deploy.yml` の signed URL 再同期が成功しているかも確認する。未設定でも承認ページ自体は動作 |
| 承認後 Teams 通知が飛ばない | `LOGIC_APP_CALLBACK_URL`、`logic-wmbvhdhcsuyb2` の run history、Teams connection `teams-1`、対象 Team / channel を確認 |
| SharePoint に保存されない | target site への permission grant か `sharepointonline` connector の認証状態を確認 |
| KB が静的レスポンス | `SEARCH_ENDPOINT` / `SEARCH_API_KEY` または Foundry の Azure AI Search 既定接続、`regulations-index` / `regulations-kb` を確認 |
