# 上司承認 workflow ガイド

このドキュメントは、上司承認 URL を上司へ届ける通知 workflow の request / callback 契約を定義します。

現在の IaC が自動作成する Logic Apps は post approval actions 専用です。上司承認通知は別 workflow として分離し、`MANAGER_APPROVAL_TRIGGER_URL` で FastAPI から呼び出します。

現在の組み込み上司承認ページは、今回承認対象の企画書と、直前までの確定済み企画書を横並びで比較表示できます。通知 workflow 側は `manager_approval_url` を上司へ届ければ十分で、比較 UI 自体はアプリ側が担当します。

2026-04-18 時点の推奨構成は、既存の `Microsoft.Web/connections/teams` 接続を使う Consumption Logic App です。現在のワークスペースには、そのまま deploy できる standalone Bicep として [infra/modules/manager-approval-notification-logic-app.bicep](../infra/modules/manager-approval-notification-logic-app.bicep) を追加しています。rebuilt `workiq-dev` tenant では `teams-1` が Connected で、`logic-manager-approval-wmbvhdhcsuyb2` が live です。

## 1. FastAPI から workflow へ送る request

FastAPI は `MANAGER_APPROVAL_TRIGGER_URL` に対して次の JSON を `POST` します。

```json
{
  "request_type": "manager_approval",
  "plan_title": "春の沖縄ファミリーキャンペーン",
  "plan_markdown": "# 春の沖縄ファミリーキャンペーン\n...",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "manager_email": "manager@example.com",
  "manager_approval_url": "https://<app-host>/?manager_conversation_id=550e8400-e29b-41d4-a716-446655440000#manager_approval_token=<shared-secret-token>",
  "manager_callback_url": "https://<app-host>/api/chat/550e8400-e29b-41d4-a716-446655440000/manager-approval-callback",
  "manager_callback_token": "<shared-secret-token>"
}
```

各フィールドの意味:

| フィールド | 必須 | 用途 |
| --- | --- | --- |
| `request_type` | 必須 | 常に `manager_approval` |
| `plan_title` | 必須 | Adaptive Card のタイトル表示 |
| `plan_markdown` | 必須 | 上司が確認する本文 |
| `conversation_id` | 必須 | callback 時に同じ ID を返す |
| `manager_email` | 必須 | 承認依頼先の Microsoft 365 メールアドレス |
| `manager_approval_url` | 必須 | 組み込みの上司承認ページ URL |
| `manager_callback_url` | 必須 | 承認結果を返す FastAPI endpoint |
| `manager_callback_token` | 必須 | callback 認証用の共有トークン |

## 2. workflow から FastAPI へ返す callback

workflow は承認または差し戻しの後、`manager_callback_url` へ `POST` します。

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "approved": false,
  "comment": "価格表現をもう少し抑えてください",
  "approver_email": "manager@example.com",
  "callback_token": "<shared-secret-token>"
}
```

補足:

- `callback_token` は request で受け取った `manager_callback_token` をそのまま返します。
- `callback_token` は JSON body でも `X-Manager-Approval-Token` ヘッダでも送れます。
- token が一致しない callback は FastAPI 側で `403 invalid manager approval token` を返します。
- `approved=true` の場合、`comment` は空でも構いません。
- `approved=false` の場合、差し戻し理由を `comment` に入れてください。

## 3. 推奨フロー

### 推奨: 通知 workflow

1. HTTP Request trigger で上記 request JSON を受け取る。
2. `manager_email` 宛てに Teams DM で `manager_approval_url` を送る。
3. workflow 自身の HTTP response は `202 Accepted` で返す。

この場合、承認 / 差し戻しの UI と callback はすべてアプリ内の上司承認ページが担当します。workflow 側で comment 組み立てや callback token の再送を実装する必要がありません。

### 推奨実装の具体形

- Teams connector の modern action `PostMessageToConversation` を使う
- `Post as = Flow bot`
- `Post in = Chat with Flow bot`
- request body に `to = manager_email` と `messageBody = manager_approval_url を含む本文` を渡す

この方式なら deprecated な `PostUserNotification` に依存せず、Adaptive Card callback も不要です。

### 代替: Teams Adaptive Card で完結させる場合

1. HTTP Request trigger で上記 request JSON を受け取る。
2. `manager_email` 宛てに Teams connector の `Post adaptive card and wait for a response` を送る。
3. 承認なら `approved=true`、差し戻しなら `approved=false` とコメントを組み立てる。
4. HTTP action で `manager_callback_url` へ callback する。
5. workflow 自身の HTTP response は `202 Accepted` で返す。

## 4. サンプル資産

外部 workflow 実装時にそのまま流用できる最小サンプルを `docs/samples/manager-approval/` に置いています。

- [request-body.json](samples/manager-approval/request-body.json): FastAPI から受け取る request の雛形
- [adaptive-card.json](samples/manager-approval/adaptive-card.json): Teams に投稿する Adaptive Card の雛形
- [callback-approved.json](samples/manager-approval/callback-approved.json): 承認時 callback の雛形
- [callback-rejected.json](samples/manager-approval/callback-rejected.json): 差し戻し時 callback の雛形

Microsoft Teams connector / Workflows の Adaptive Card アクションでは template 機能を前提にしない方が安全なので、サンプルは `<<PLACEHOLDER>>` 形式にしています。Workflow 側で trigger body や action output に置き換えてください。

代表的な置換先:

| Placeholder | 置換元の例 |
| --- | --- |
| `<<PLAN_TITLE>>` | `plan_title` |
| `<<PLAN_EXCERPT>>` | `plan_markdown` を 600〜1000 文字程度に要約または切り詰めた値 |
| `<<CONVERSATION_ID>>` | `conversation_id` |
| `<<MANAGER_EMAIL>>` | `manager_email` |
| `<<MANAGER_APPROVAL_URL>>` | `manager_approval_url` |
| `<<MANAGER_CALLBACK_URL>>` | `manager_callback_url` |
| `<<MANAGER_CALLBACK_TOKEN>>` | `manager_callback_token` |
| `<<RESPONDER_EMAIL>>` | Teams action の responder email / UPN |
| `<<MANAGER_COMMENT>>` | Adaptive Card 入力 `managerComment` |

## 5. Adaptive Card に最低限入れる情報

- 企画書タイトル
- 企画書サマリーまたは先頭数段落
- 承認ボタン
- 差し戻しボタン
- 差し戻しコメント入力欄

FastAPI は manager approval の待機中、UI 上では待機表示だけを出します。承認 / 差し戻しの操作は Teams 側だけで完結させてください。

実装メモ:

- `plan_markdown` 全文をそのままカードに載せると可読性が落ちるため、workflow 側で要約または先頭抜粋を作る方が扱いやすいです。
- Adaptive Card の `Action.Submit` は 1 枚につき最初の 1 回だけ受理される想定で設計してください。
- update message を設定し、回答後に「承認済み」または「差し戻し済み」と明示する運用を推奨します。

## 5.1 組み込みの上司承認ページ

このリポジトリには、workflow がなくても本番運用できる上司承認ページが含まれています。`manager_approval_url` を上司へ共有すれば、そのページから直接承認 / 差し戻しできます。

- token は URL fragment (`#manager_approval_token=...`) に入るため、通常の HTTP リクエストやサーバーアクセスログには乗りません。
- 承認ページは `GET /api/chat/{thread_id}/manager-approval-request` で企画書を取得し、決定時は `POST /api/chat/{thread_id}/manager-approval-callback` を呼びます。
- `MANAGER_APPROVAL_TRIGGER_URL` を設定しない場合でも、この承認ページ URL を担当者が共有すれば運用できます。
- `GET /api/chat/{thread_id}/manager-approval-request` のレスポンスには `current_version` と `previous_versions` が含まれ、2 回目以降の上司承認では前回確定版との比較をそのまま表示できます。

## 6. セキュリティ注意点

- `manager_callback_token` は secret と同等に扱い、Teams 本文や監査ログへ出さない。
- `manager_approval_url` は manager にだけ共有する。token 自体は URL fragment に入っているが、チャットや監査ログへ転記しない。
- workflow 実行ログに request body 全体を残す場合は token をマスクする。
- callback 先は HTTPS を使う。
- `conversation_id` だけで callback しない。必ず token を返す。

## 7. デプロイ後の設定

### 7.1 standalone Bicep で workflow を作る

既存の `teams` 接続をそのまま使う場合、次で通知 workflow を作成できます。

```powershell
az deployment group create \
  --resource-group rg-dev \
  --template-file infra/modules/manager-approval-notification-logic-app.bicep \
  --parameters name=logic-manager-approval-5gg4m4g72lrdo \
               location=eastus2 \
               teamsConnectionId=/subscriptions/<subscription-id>/resourceGroups/rg-dev/providers/Microsoft.Web/connections/teams \
               teamsManagedApiId=/subscriptions/<subscription-id>/providers/Microsoft.Web/locations/eastus2/managedApis/teams
```

`<subscription-id>` には対象サブスクリプション ID を入れてください。

### 7.2 trigger URL をアプリへ渡す

workflow の HTTP trigger URL を Container App に渡します。

```bash
azd env set MANAGER_APPROVAL_TRIGGER_URL https://<teams-enabled-manager-approval-workflow-url>
```

GitHub Actions から本番へ流す場合は、同じ値を repository または environment secret の `MANAGER_APPROVAL_TRIGGER_URL` に登録してください。

## 7.3 新しい tenant で Teams 接続を作り直す場合

rebuilt `workiq-dev` tenant では `teams-1` がすでに Connected なので、この節の作業は不要です。別 tenant を作り直す場合や、Teams connection が切れた場合だけ tenant 側の対話認証が必要です。最短で必要なのは次の作業です。

1. Teams 管理者または利用可能なアカウントで、Workflows アプリが Teams で許可されていることを確認する。
2. Logic Apps Standard または Power Automate の designer で、Microsoft Teams connector のアクションを 1 つ追加する。
3. 接続作成画面が出たら、実際に通知を送る Microsoft 365 アカウントでサインインする。
4. 接続状態が Connected になったことを確認する。
5. HTTP Request trigger を持つ workflow を保存し、trigger URL を取得する。

こちらに返してほしい情報は次の 3 つです。

1. workflow の HTTP trigger URL
2. 接続に使った送信元アカウントのメールアドレス
3. テストに使う上司アカウントのメールアドレス 1 件

補足:

- Microsoft Teams connector の接続は shareable ではないため、接続を作る本人のサインインが必要です。
- Teams の一部アクションは Workflows アプリが Teams admin center で allow 状態であることを前提にします。
- ここが終われば、このリポジトリ側の MANAGER_APPROVAL_TRIGGER_URL 設定とアプリ連携は継続できます。

## 8. ローカル E2E 用 mock workflow

tenant 固有の Teams connector 認可がまだない段階でも、同じ request / callback 契約で上司承認フローを最後まで確認できるように、[scripts/mock_manager_approval_workflow.py](../scripts/mock_manager_approval_workflow.py) を追加しています。

PowerShell 例:

```powershell
$env:MOCK_MANAGER_APPROVAL_DECISION = "approve"
$env:MOCK_MANAGER_APPROVAL_DELAY_SECONDS = "2"
uv run uvicorn scripts.mock_manager_approval_workflow:app --host 127.0.0.1 --port 8010
```

別ターミナルでアプリ本体に外部 workflow URL を向けます。

```powershell
$env:MANAGER_APPROVAL_TRIGGER_URL = "http://127.0.0.1:8010/manager-approval"
uv run uvicorn src.main:app --reload --port 8000
```

mock workflow が見る環境変数:

- `MOCK_MANAGER_APPROVAL_DECISION`: `approve` または `reject`。既定は `approve`
- `MOCK_MANAGER_APPROVAL_COMMENT`: `reject` 時の差し戻しコメント。未設定時は既定文言を使用
- `MOCK_MANAGER_APPROVAL_APPROVER_EMAIL`: callback に載せる承認者メール。未設定時は `manager_email` を再利用
- `MOCK_MANAGER_APPROVAL_DELAY_SECONDS`: callback までの待機秒数。既定は `2`。会話保存より先に callback しないよう、既定値を 0 ではなく少し持たせています

この mock は Teams 投稿を行わず、受け取った request をもとに遅延後 callback だけを返します。HTTP 契約、callback token、差し戻し UI の確認にはこれで十分です。
