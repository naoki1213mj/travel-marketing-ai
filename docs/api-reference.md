# API リファレンス

REST API と SSE イベントの仕様です。

## ベース URL

- ローカル: `http://localhost:8000`
- Azure: `https://<container-app-fqdn>`

## 実行モード

| モード | 条件 | 挙動 |
| --- | --- | --- |
| Azure 本番 / Azure 接続モード | `AZURE_AI_PROJECT_ENDPOINT` が設定済み | FastAPI オーケストレーションで主フローを実行。最終承認後はブローシャ生成完了時点で `done` を返し、動画・品質レビュー・承認後アクションは background update として後続追記されることがある |
| モック / デモモード | `AZURE_AI_PROJECT_ENDPOINT` 未設定 | ハードコード済みの SSE イベントを返す |
| 修正 / 改善モード | `POST /api/chat` に `conversation_id` を指定 | 承認待ち中や評価フィードバックでは `marketing-plan-agent` を再実行して新しい `approval_request` を返す。`IMPROVEMENT_MCP_ENDPOINT` が有効なら評価フィードバック時に `generate_improvement_brief` を先に呼び、失敗時は従来ロジックへフォールバックする。通常の修正指示はキーワードに応じて `marketing-plan-agent` / `regulation-check-agent` / `brochure-gen-agent` を再実行 |
| 承認継続モード | `POST /api/chat/{thread_id}/approve` | 承認なら Agent3a→Agent3b→Agent4→Agent5、非承認なら企画書を再生成して再度 `approval_request` を返す |

## エンドポイント一覧

| メソッド | パス | 説明 |
| --- | --- | --- |
| `GET` | `/api/health` | ライブネスプローブ |
| `GET` | `/api/ready` | 本番必須設定の readiness チェック |
| `GET` | `/api/capabilities` | feature flag / 接続状態から算出した安全な機能可用性 |
| `POST` | `/api/chat` | メインチャット SSE |
| `POST` | `/api/chat/{thread_id}/approve` | 承認継続または修正継続 |
| `GET` | `/api/chat/{thread_id}/manager-approval-request` | 上司承認ページ用の企画書取得 |
| `POST` | `/api/chat/{thread_id}/manager-approval-callback` | 上司承認 workflow からの承認結果コールバック |
| `GET` | `/api/conversations` | 会話一覧取得 |
| `GET` | `/api/conversations/{conversation_id}` | 会話詳細取得（履歴復元用） |
| `GET` | `/api/replay/{conversation_id}` | 保存済み SSE リプレイ |
| `GET` | `/api/sources/limits` | source ingestion の有効状態と運用上限 |
| `POST` | `/api/sources/text` | ユーザー提供テキストをレビュー待ち source として登録 |
| `POST` | `/api/sources/pdf` | PDF を Content Understanding で解析しレビュー待ち source として登録 |
| `POST` | `/api/sources/audio` | 短命 `audio_url` を MAI Transcribe で文字起こししレビュー待ち source として登録 |
| `GET` | `/api/sources` | owner scope 内の source 一覧 |
| `GET` | `/api/sources/{source_id}` | owner scope 内の source 詳細 |
| `POST` | `/api/sources/{source_id}/review` | source summary の承認 / 却下 |
| `DELETE` | `/api/sources/{source_id}` | owner scope 内の source 削除 |
| `GET` | `/api/voice-token` | 廃止済み endpoint（常に `410 Gone`） |
| `GET` | `/api/voice-config` | Voice Live MSAL 設定取得 |
| `POST` | `/api/evaluate` | 品質評価の実行 |
| `POST` | `/api/upload-pdf` | 旧 PDF アップロード互換 route（source draft を返す） |

## ヘルスチェック

### `GET /api/health`

常に `200 OK` を返すライブネスチェックです。

```json
{"status": "ok"}
```

### `GET /api/ready`

本番相当環境 (`ENVIRONMENT=production|prod|staging`) で必須設定が揃っているかを返します。

正常時:

```json
{"status": "ready", "missing": []}
```

不足時:

```json
{
  "status": "degraded",
  "missing": ["AZURE_AI_PROJECT_ENDPOINT"]
}
```

## `GET /api/capabilities`

ロードアウト中の機能を UI が安全に表示するため、secret や endpoint 値を含めず `available` / `configured` だけを返します。`available=true` は「必要な feature flag と必須接続が揃った」ことを表し、production-ready や利用承認済みを保証するものではありません。

主な feature key:

| Key | `available=true` の条件 |
| --- | --- |
| `model_router` | `ENABLE_MODEL_ROUTER=true` と project endpoint / router deployment が設定済み |
| `gpt_55` | `ENABLE_GPT_55=true` と project endpoint が設定済み（実 deployment/quota は Azure 側で別途確認） |
| `foundry_tracing` | `ENABLE_FOUNDRY_TRACING=true` と App Insights 関連付けが有効 |
| `evaluation_logging` | `ENABLE_EVALUATION_LOGGING=true` と `AZURE_AI_PROJECT_ENDPOINT` が設定済み |
| `continuous_monitoring` | `ENABLE_CONTINUOUS_MONITORING=true`、評価ログ opt-in、project endpoint、`CONTINUOUS_MONITORING_SAMPLE_RATE > 0` |
| `cost_metrics` | `ENABLE_COST_METRICS=true` と `APPLICATIONINSIGHTS_CONNECTION_STRING` が設定済み。token usage からの概算で、請求データではありません |
| `source_ingestion` | `ENABLE_SOURCE_INGESTION=true` |
| `voice_live` | project endpoint と Entra SPA client id が設定済み |
| `voice_talk_to_start` | `ENABLE_VOICE_TALK_TO_START=true` かつ Voice Live が利用可能 |
| `mai_transcribe_1` | `ENABLE_MAI_TRANSCRIBE_1=true`、endpoint、deployment、確認済み API path が設定済み |
| `work_iq` | Entra SPA client id と `MARKETING_PLAN_RUNTIME=foundry_preprovisioned`、`WORKIQ_RUNTIME=foundry_tool` + project endpoint、または明示 rollback の `graph_prefetch` |

レスポンス例:

```json
{
  "version": 1,
  "features": {
    "source_ingestion": {"available": false, "configured": false},
    "mai_transcribe_1": {"available": false, "configured": false},
    "continuous_monitoring": {"available": false, "configured": false}
  }
}
```

## `POST /api/chat`

ユーザーメッセージを受け取り、SSE ストリームを返します。

- レート制限: 10 リクエスト / 分
- 入力は制御文字除去と軽量な注入ガードを通過したものだけが実行されます

### `/api/chat` リクエストボディ

```json
{
  "message": "沖縄のファミリー向け春キャンペーンを企画してください",
  "conversation_id": null,
  "user_settings": {
    "model": "gpt-5-4-mini",
    "temperature": 0.2,
    "max_tokens": 1200,
    "top_p": 1.0,
    "iq_search_results": 5,
    "iq_score_threshold": 0.0,
    "image_settings": {
      "image_model": "gpt-image-2",
      "image_quality": "medium",
      "image_width": 1024,
      "image_height": 1024
    }
  },
  "conversation_settings": {
    "work_iq_enabled": true,
    "source_scope": ["emails", "teams_chats"]
  },
  "workflow_settings": {
    "manager_approval_enabled": true,
    "manager_email": "manager@example.com",
    "marketing_plan_runtime": "foundry_preprovisioned",
    "work_iq_runtime": "foundry_tool"
  }
}
```

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `message` | `string` | 必須 | 1 文字以上 |
| `conversation_id` | `string \| null` | 任意 | 既存会話 ID を指定すると修正モード |
| `user_settings` | `object \| null` | 任意 | 会話途中でも変更可能なモデル設定。`model`（`gpt-5-4-mini`、`gpt-5.5`、`gpt-5.4`、`gpt-4-1-mini`、`gpt-4.1`。`gpt-5.5` は対象 Foundry account の deployment/quota が必要）、`temperature`、`max_tokens`、`top_p`、`iq_search_results`、`iq_score_threshold`、`image_settings` を送信できる |
| `user_settings.image_settings` | `object \| null` | 任意 | 画像生成設定。`image_model`（`gpt-image-1.5` / `gpt-image-2` / `MAI-Image-2`）、`image_quality`（`low`/`medium`/`high`、GPT 系のみ）、`image_width`/`image_height`（MAI のみ、最小 768、w×h ≤ 1,048,576） |
| `conversation_settings` | `object \| null` | 任意 | 新規会話時だけ受理する固定設定。現状は `work_iq_enabled` と `source_scope` を含む |
| `settings` | `object \| null` | 任意 | 旧互換。`user_settings` / `conversation_settings` へ段階移行中 |
| `workflow_settings` | `object \| null` | 任意 | 承認フローと runtime override。`manager_approval_enabled=true` の場合は `manager_email` が必須。加えて `marketing_plan_runtime`（`legacy` / `foundry_preprovisioned`。後方互換で `foundry_prompt` も受理）と `work_iq_runtime`（`graph_prefetch` / `foundry_tool`）を会話ごとに上書きできます。`work_iq_runtime=foundry_tool` は `marketing_plan_runtime=foundry_preprovisioned` が前提です。`MANAGER_APPROVAL_TRIGGER_URL` は通知 workflow を使う場合だけ設定します |

### `/api/chat` 追加ヘッダ

| ヘッダ | 必須 | 用途 |
| --- | --- | --- |
| `Authorization: Bearer <token>` | 任意 | Work IQ を有効化した **新規会話** で使う本人の delegated token。`work_iq_runtime=foundry_tool` では **Foundry data-plane (`https://ai.azure.com/user_impersonation`) token**、`graph_prefetch` では Graph delegated token を送ります。`foundry_tool` はこのヘッダの有無で fail-closed 判定し、同意や接続不備は Foundry の `oauth_consent_request` / tool error として返します |
| `X-Work-IQ-Graph-Authorization: Bearer <Graph token>` | 任意 | `graph_prefetch` rollback を明示利用するときの Graph delegated token。`foundry_tool` の通常経路では不要です |
| `X-Work-IQ-Auth-Status` | 任意 | フロントエンド preflight の結果。`authenticated` / `auth_required` / `consent_required` / `redirecting` / `failed` を backend の session 復元と UI 状態整合に使います |
| `X-User-Timezone` | 任意 | rollback の `graph_prefetch` で Work IQ brief を取得するときの `locationHint.timeZone` に使用（未指定時は `UTC`） |

> フロントエンドは Work IQ 有効化時に認証 preflight を行い、`auth_required` / `consent_required` / `redirecting` を UI へ先に反映します。`redirecting` の場合は Entra サインインへ遷移するため、この `/api/chat` リクエスト自体は送信されません。

### `/api/chat` 現行挙動

| 条件 | SSE の主な流れ |
| --- | --- |
| 新規 + Azure 接続あり | `agent_progress` → `text` → `approval_request` → （承認後）`text` → `done`。`background_updates_pending=true` の場合は、その後に `video-gen-agent` や `quality-review-agent` の `text` が同じ会話へ追記される |
| 新規 + Azure 接続なし | モックの各エージェント進捗と `approval_request` |
| `conversation_id` + 承認待ち中 | `marketing-plan-agent` で企画書を再生成し、新しい `approval_request` を返す |
| `conversation_id` + 完了済み + 評価フィードバック | `marketing-plan-agent` で企画書を改善し、新しい `approval_request` を返す |
| `conversation_id` + 完了済み + 通常の修正指示 | キーワードに応じて `marketing-plan-agent` / `regulation-check-agent` / `brochure-gen-agent` を再実行 |

### `/api/chat` 注意

- Azure モードの主フローは Agent2（施策生成）完了後に担当者向け `approval_request` を返します。
- 既定値は `marketing_plan_runtime=foundry_preprovisioned` + `work_iq_runtime=foundry_tool` です。Agent2 は Work IQ の有無にかかわらず `postprovision.py` で同期した事前作成済み Foundry Prompt Agent を `agent_reference` で実行し、Work IQ 有効時は **ユーザーの Foundry delegated token** で Responses API を呼びつつ、agent definition に含まれる Work IQ MCP connection を使います。Work IQ で同意が必要な場合は Foundry の `oauth_consent_request` を UI に返し、ユーザー同意後に同じ会話を再実行します。
- `work_iq_runtime=graph_prefetch` は **明示 rollback 専用** の経路で、この場合だけ Agent1 と Agent2 の間で Microsoft Graph Copilot Chat API（`POST /beta/copilot/conversations` → `POST /beta/copilot/conversations/{id}/chatOverStream`、必要時 `/chat` へフォールバック）から短い workplace brief を取得し、Agent2 prompt にだけ注入します。既定 timeout は `120` 秒です。`foundry_tool` 失敗時に自動で silent fallback することはありません。
- `work_iq_runtime=foundry_tool` を `marketing_plan_runtime=legacy` と組み合わせた request はバリデーションエラーになります。
- `work_iq_runtime=foundry_tool` は fail-closed です。認証不足・同意不足・接続不備・`WORKIQ_NOT_USED` はエラーとして会話を止めます。**brief なしで継続** するのは `graph_prefetch` rollback 経路だけで、その場合は `tool_event.source="workiq"` の status を返します。
- 担当者承認後は Agent3a → Agent3b を実行し、`workflow_settings.manager_approval_enabled=true` の場合は manager approval 用の `approval_request` を返して待機します。
- manager approval の `approval_request` には `approval_scope=manager`、`manager_email`、`manager_approval_url` が含まれます。`MANAGER_APPROVAL_TRIGGER_URL` が設定されていれば通知 workflow も同時に呼ばれ、未設定または送信失敗時は共有リンク運用にフォールバックします。
- `conversation_id` を指定した修正モードでも、評価フィードバック（`品質評価` または `evaluation` を含む文）は特別扱いで、企画書再生成 → 再承認フローに戻ります。
- `IMPROVEMENT_MCP_ENDPOINT` が設定されている場合、評価フィードバックでは保存済み評価結果・規制要約・差し戻し履歴をまとめて APIM 配下の MCP `generate_improvement_brief` に渡し、成功時は `tool_event` を 1 件返します。
- Work IQ の raw context は SSE や会話履歴には保存されず、`tool_event.source="workiq"` の status と brief summary / source metadata だけが保存されます。バックエンドは `metadata.work_iq_session` の status を継続保存するため、`restoreConversation()` 後も Work IQ の UI 表示は同じ状態に戻ります。
- Foundry / Web Search 由来の citation placeholder（例: `citeturn0search0`）は、Agent2 の結果抽出時点で除去されます。SSE、承認待ち payload、会話履歴、下流 Agent3/4 には正規化済みの企画書 Markdown だけが渡ります。
- Foundry が返す Work IQ 認可リンクは、フロントエンドで `https://login.microsoftonline.com` または `https://login.microsoft.com` の HTTPS URL に限定してから遷移します。それ以外の URL は `WORKIQ_AUTH_REDIRECT_BLOCKED` として停止します。
- フロントエンドは各 `done` イベントのたびに成果物スナップショットを保持し、v1 / v2 / ... を切り替えます。
- 2 回目以降の上司承認待ちでは、`GET /api/conversations/{id}` のイベント列から未確定ラウンドを復元し、直前の確定版を `pendingVersion` として保持します。

### cURL 例

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"沖縄のファミリー向け春キャンペーンを企画してください"}'
```

## `POST /api/chat/{thread_id}/approve`

承認継続または修正継続のための SSE エンドポイントです。

- レート制限: 10 リクエスト / 分
- `response` も軽量な注入ガード対象です
- パスの `thread_id` が実際に使われる会話 ID です

### `/api/chat/{thread_id}/approve` リクエストボディ

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "承認",
  "approval_token": "ee_6D5JuLZ_4JZG0-KPq..."
}
```

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `conversation_id` | `string` | 必須 | バリデーション用に受け取る互換フィールド。実体は path の `{thread_id}` を使う |
| `response` | `string` | 必須 | 承認キーワードまたは修正指示 |
| `approval_token` | `string` | 匿名 lookup で必須 | `chat()` が `approval_request` SSE イベントで配布した per-conversation の bearer token (32 byte urlsafe)。`_refine_events()` で revision 毎に rotation する。Entra Bearer 認証済の実ユーザー (`user-*`) は owner_id 一致で代替可、匿名 (`anon-*`) は token 必須 |

### 承認判定キーワード

以下のいずれかを部分一致で含むと承認扱いになります。

- `承認`
- `了承`
- `進めて`
- `approve`
- `approved`
- `go`
- `ok`
- `yes`
- `批准`
- `同意`

### `/api/chat/{thread_id}/approve` 現行挙動

| 条件 | 挙動 |
| --- | --- |
| 承認 + 有効な `approval_token` (or 認証済ユーザーの owner 一致) | `regulation-check-agent` → `plan-revision-agent` を実行し、上司承認オフなら `brochure-gen-agent` → `video-gen-agent` を続行。上司承認オンなら manager approval の `approval_request` で待機し、通知 workflow があれば併せて呼び出す |
| 承認 + 匿名 + token 不在 / 不一致 | `error` イベント `code: APPROVAL_CONTEXT_NOT_FOUND` を返却。pipeline 後段は実行されない |
| 承認 + Azure 接続なし | モックの Agent3a → Agent3b → Agent4 → Agent5 イベントを返す |
| 非承認 | 修正テキストとして扱い、`_refine_events()` で再調整。新しい `approval_token` が発行されるので client 側は次回 approve でその新 token を echo すること |

### 主な error code

- `APPROVAL_CONTEXT_NOT_FOUND`: 該当する pending approval が in-memory にも Cosmos にも見つからない、または匿名 lookup で `approval_token` が無い / 不一致 / Cosmos doc の `metadata.pending_approval_token` と不一致
- `INPUT_GUARD_BLOCKED`: response 文字列が prompt shield 軽量ガードに弾かれた

## `GET /api/chat/{thread_id}/manager-approval-request`

上司承認ページが企画書本文を取得するための JSON API です。`X-Manager-Approval-Token` ヘッダ、または body の `manager_approval_token` で token を渡します。

> **Security note**: クエリ文字列 (`?token=...`) からの token 受け取りは Application Insights のリクエストログ・referer ヘッダー・ブラウザ履歴に平文 token が漏洩するリスクがあるため受け入れません (SEC-H4)。

レスポンス例:

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "current_version": 2,
  "plan_title": "春の沖縄ファミリーキャンペーン",
  "plan_markdown": "# 春の沖縄ファミリーキャンペーン\n...",
  "manager_email": "manager@example.com",
  "previous_versions": [
    {
      "version": 1,
      "plan_title": "初版企画書",
      "plan_markdown": "# 初版企画書\n..."
    }
  ]
}
```

- `current_version` は今回承認対象の版番号です。
- `previous_versions` は上司承認ポータルで比較表示するための確定済み企画書一覧です。
- 永続ストア反映前のタイミングでも、バックエンドは pending approval context に保存した `previous_versions` をフォールバックとして返します。

## `POST /api/chat/{thread_id}/manager-approval-callback`

Teams 対応の上司承認 workflow から承認結果を受け取る JSON API です。

### `manager-approval-callback` リクエストボディ

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "approved": false,
  "comment": "価格表現をもう少し抑えてください",
  "approver_email": "manager@example.com",
  "callback_token": "<manager-callback-token>"
}
```

### `manager-approval-callback` 現行挙動

| 条件 | 挙動 |
| --- | --- |
| `approved=true` | バックグラウンドで Agent4 → Agent5 と post approval actions を再開し、HTTP レスポンスは `{"status":"accepted","conversation_id":"..."}` を返す |
| `approved=false` | 担当者向け `approval_request` を会話履歴へ追記し、差し戻しコメントを UI に戻す。HTTP レスポンスは `{"status":"reopened","conversation_id":"..."}` を返す |

### `manager-approval-callback` 注意

- `callback_token` は manager approval workflow へ最初に渡した `manager_callback_token` をそのまま返してください。
- 代わりに `X-Manager-Approval-Token` ヘッダで送っても受け付けます。
- 組み込みの上司承認ページもこの endpoint をそのまま利用します。
- token がない、または一致しない callback は `403 invalid manager approval token` で拒否されます。
- callback に Bearer token を付ける場合、会話 owner と一致しない呼び出しは `403 conversation owner mismatch` で拒否されます。

## 会話 API

### `GET /api/conversations`

会話一覧を返します。Cosmos DB が未設定ならインメモリから返します。

- `/api/conversations`、`/api/conversations/{id}`、`/api/replay/{id}` はすべて owner-bound です。delegated Bearer token がある場合はその owner に属する会話だけが返り、トークンがない場合は匿名/fallback owner の会話だけが見えることがあります。

クエリ:

| パラメータ | 型 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `limit` | `int` | `20` | 最大件数 |

レスポンス例:

```json
{
  "conversations": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "input": "沖縄のファミリー向け春キャンペーン",
      "status": "completed",
      "created_at": "2026-03-20T10:30:00+00:00"
    }
  ]
}
```

### `GET /api/conversations/{conversation_id}`

会話ドキュメント全体を返します。フロントエンドの `restoreConversation()` はこのエンドポイントから保存済みイベントを取得し、再推論なしで会話状態を復元します。

レスポンス例:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-03-20T10:30:00+00:00",
  "updated_at": "2026-03-20T10:31:10+00:00",
  "status": "completed",
  "input": "沖縄のファミリー向け春キャンペーン",
  "messages": [],
  "artifacts": [
    {
      "version": 1,
      "created_at": "2026-03-20T10:31:05+00:00"
    }
  ],
  "metadata": {
    "background_updates_pending": false,
    "conversation_settings": {
      "work_iq_enabled": true,
      "source_scope": ["emails"]
    },
    "work_iq_session": {
      "enabled": true,
      "source_scope": ["emails"],
      "auth_mode": "delegated",
      "brief_summary": "営業メールでは家族向け訴求が重視されていました。",
      "status": "completed"
    }
  }
}
```

- `metadata.background_updates_pending=true` の場合、ユーザー向けの `done` 後も動画 URL や品質レビューが後続で追記される可能性があります。
- `manager_approval_callback_token` のような機密 metadata はこの API では自動的に除去されます。
- `metadata.work_iq_session` には raw workplace context は含まれず、brief summary / source metadata / status だけが返ります。brief が無い `foundry_tool` 経路でも `status` / `source_scope` は保持され、復元後の UI 一貫性に使われます。

未存在時:

```json
{"error": "conversation not found"}
```

### `GET /api/replay/{conversation_id}`

保存済み SSE を `speed` 倍速でリプレイします。

- Work IQ 会話でも replay には raw workplace context は含まれません。

| パラメータ | 型 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `speed` | `float` | `5.0` | リプレイ倍率 |

データがない場合は `error` イベントで以下を返します。

```json
{
  "message": "リプレイデータが見つかりません",
  "code": "REPLAY_NOT_FOUND"
}
```

## `POST /api/evaluate`

企画書とブローシャを評価し、Built-in 指標、カスタム指標、LLM ジャッジ結果をまとめて返します。

- レート制限: 5 リクエスト / 分
- フロントエンドでは専用の Evaluation タブから呼ばれます
- `AZURE_AI_PROJECT_ENDPOINT` が未設定でも呼び出せますが、Built-in 評価と prompt-based 評価はエラー / 低機能モードになります
- Work IQ を有効化した会話の評価保存では、同じ delegated Bearer token を付与して owner-bound conversation に紐づけます
- Foundry への評価ログ送信は `ENABLE_EVALUATION_LOGGING=true` の明示 opt-in 時のみバックグラウンド実行します。送信 payload は raw prompt / raw Work IQ content / transcript / bearer token / brochure HTML を含めず、スコア・件数・finding/evidence ID に最小化します
- 継続監視は `ENABLE_CONTINUOUS_MONITORING=true` かつ評価ログ opt-in 時のみ、`CONTINUOUS_MONITORING_SAMPLE_RATE` で決定的サンプリングし、応答をブロックせずに最小 payload を非同期送信します
- 評価ログの保持目安は `EVALUATION_LOG_RETENTION_DAYS`（既定 30 日）で文書化し、Foundry project 側の保持/削除運用と合わせて管理します

### フロントエンドでの表示ルール

- ラウンド比較は API ではなくフロントエンド側の責務です。保存済みバージョンごとの評価結果を比較して差分 UI を構成します。
- 評価パネルの比較対象を切り替えても、右側の成果物プレビュー自体は切り替わりません。成果物全体の切替は `VersionSelector` が担当します。
- 比較 UI は「現在の版」と「比較対象版」を上部の要約カードで並べて表示し、その下に改善 / 悪化 / 変化なしの集計と指標差分を出します。
- `builtin` に `task_adherence` が返ってくる場合でも、現行フロントエンドでは比較・総合スコア・改善フィードバック生成には使いません。

### `/api/evaluate` リクエストボディ

```json
{
  "query": "沖縄のファミリー向け春キャンペーンを企画してください",
  "response": "# 春の沖縄ファミリープラン\n\n...",
  "html": "<!DOCTYPE html><html lang=\"ja\">...</html>",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "artifact_version": 2
}
```

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `query` | `string` | 必須 | 元のユーザー依頼 |
| `response` | `string` | 必須 | 企画書 Markdown |
| `html` | `string` | 任意 | ブローシャ HTML。空文字可 |
| `conversation_id` | `string \| null` | 任意 | 評価結果を保存する会話 ID。指定すると owner-bound conversation に `evaluation_result` イベントとして追記される |
| `artifact_version` | `int \| null` | 任意 | 評価対象の成果物バージョン。`conversation_id` と併用した場合に評価履歴と悪化検知の比較対象を決める |
| `evidence` | `array` | 任意 | 評価 UI / findings に使う根拠ソース。`id`、`title`、`source`、`url`、`quote`、`relevance`、`metadata` を安全 schema に正規化する |
| `charts` | `array` | 任意 | 評価 UI / findings に使うチャート仕様。`chart_type`、`title`、`series`、`data`、`metadata` を安全 schema に正規化する |

### レスポンス例

```json
{
  "custom": {
    "travel_law_compliance": {
      "score": 0.8,
      "details": {
        "旅行業登録番号": true,
        "取引条件": true,
        "取消料": false,
        "旅程": true,
        "価格表示": true
      },
      "reason": "5 項目中 4 項目が記載されています"
    },
    "conversion_potential": {
      "score": 0.6,
      "details": {
        "CTA（予約導線）": true,
        "価格表示の明確さ": true,
        "限定感の訴求": false,
        "特典・付加価値": true,
        "安心感の提供": false
      },
      "reason": "5 項目中 3 項目が含まれています"
    }
  },
  "builtin": {
    "relevance": { "score": 4.0, "reason": "依頼に沿っています" },
    "coherence": { "score": 4.5, "reason": "構成が自然です" },
    "fluency": { "score": 4.2, "reason": "日本語表現が滑らかです" }
  },
  "marketing_quality": {
    "appeal": 4,
    "differentiation": 3,
    "kpi_validity": 4,
    "brand_tone": 5,
    "overall": 4,
    "reason": "訴求は強いが差別化の具体性に改善余地があります"
  },
  "plan_quality": {
    "overall": 4.05,
    "summary": "優先補強ポイント: 差別化、KPI 根拠の明確さ",
    "focus_areas": ["差別化", "KPI 根拠の明確さ"],
    "metrics": {
      "relevance": { "label": "依頼適合性", "score": 4.0, "reason": "依頼に沿っています" }
    }
  },
  "asset_quality": {
    "overall": 3.8,
    "summary": "優先補強ポイント: 予約導線の明確さ",
    "focus_areas": ["予約導線の明確さ"],
    "metrics": {
      "cta_visibility": { "label": "予約導線の明確さ", "score": 3.5, "reason": "CTA の配置が弱いです" }
    }
  },
  "regression_guard": {
    "summary": "前 version と比較して大きな悪化はありません。",
    "has_regressions": false,
    "degraded_metrics": [],
    "improved_metrics": [],
    "plan_overall_delta": 0.0,
    "asset_overall_delta": 0.0
  },
  "legacy_overall": 3.93,
  "evaluation_meta": {
    "version": 2,
    "round": 1,
    "created_at": "2026-03-20T10:40:00+00:00"
  }
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `custom` | `object` | code-based カスタム評価。`plan_structure_readiness` / `target_fit_readiness` / `kpi_evidence_readiness` / `offer_specificity` / `travel_law_compliance` / `cta_visibility` / `value_visibility` / `trust_signal_presence` / `disclosure_completeness` / `accessibility_readiness` と、旧 UI 互換の `conversion_potential` alias を返す |
| `builtin` | `object` | `azure-ai-evaluation` による Built-in 指標。現行フロントエンドの主要表示対象は `relevance` / `coherence` / `fluency` で、`task_adherence` が含まれていても UI 比較と総合集計からは除外される |
| `marketing_quality` | `object` | prompt-based LLM ジャッジ。`appeal` / `differentiation` / `kpi_validity` / `brand_tone` / `overall` |
| `plan_quality` | `object` | 企画書向けの集約カテゴリ。`overall` / `summary` / `focus_areas` / `metrics` を返す |
| `asset_quality` | `object` | ブローシャ/成果物向けの集約カテゴリ。`overall` / `summary` / `focus_areas` / `metrics` を返す |
| `evidence_quality` | `object` | 根拠・チャート・finding 紐づきの集約カテゴリ |
| `findings` | `array` | `status`、`confidence`、`evidence_ids` を含む評価指摘。raw evidence 本文ではなく正規化済み ID / 短い summary を使う |
| `evidence` | `array` | リクエストまたは会話履歴から復元した安全な根拠 source。secret query、token、raw transcript、brochure HTML は正規化時に除去される |
| `charts` | `array` | 評価に使った安全な chart spec |
| `regression_guard` | `object` | 前 version 比の悪化/改善検知。`has_regressions`、`degraded_metrics`、`improved_metrics`、overall delta を返す |
| `legacy_overall` | `number` | 旧 UI 互換の総合スコア |
| `evaluation_meta` | `object \| null` | 保存成功時の version / round / created_at。`conversation_id` と `artifact_version` を送らない場合は `null` |

## Source ingestion API

ユーザー提供ソースをチャット文脈へ追加するための default-off API 群です。`ENABLE_SOURCE_INGESTION=true` を明示した環境だけ有効で、未設定時は `503 SOURCE_INGESTION_DISABLED` を返します。source は owner scope、TTL、件数上限で管理し、API payload には raw text / raw transcript / raw audio URI を含めません。レビュー承認済み summary だけが `/api/chat` の追加入力に使われます。

owner boundary:

- `REQUIRE_AUTHENTICATED_OWNER=true` では認証済み Bearer token が必要です。本番相当環境でも、このフラグが未設定なら Work IQ off の通常チャットは匿名 owner として開始できます。
- JWT claim は `TRUST_AUTH_HEADER_CLAIMS=true` または `TRUSTED_AUTH_HEADER_NAME` / `TRUSTED_AUTH_HEADER_VALUE` で検証済み境界がある場合だけ信頼します。
- tenant が `ENTRA_TENANT_ID` と一致しない場合は `IDENTITY_MISMATCH`、未検証 token は `AUTH_HEADER_UNTRUSTED` です。

### `GET /api/sources/limits`

secret を含まない有効状態と運用上限を返します。

```json
{
  "enabled": false,
  "limits": {
    "max_items_per_owner": 20,
    "ttl_seconds": 604800,
    "max_text_chars": 20000,
    "max_pdf_bytes": 10485760,
    "max_audio_seconds": 1800,
    "max_audio_bytes": 26214400
  }
}
```

### `POST /api/sources/text`

```json
{
  "conversation_id": "conv-123",
  "title": "顧客ヒアリング",
  "text": "春休みは沖縄で自然体験を重視したい。",
  "metadata": {"channel": "interview"}
}
```

レスポンスは `201` で `source.status="pending_review"` を返します。本文は軽量入力ガード対象で、注入疑いは `400 SOURCE_GUARD_BLOCKED` です。

### `POST /api/sources/pdf`

`multipart/form-data` の `file` と任意の `conversation_id` を受け取ります。拡張子、content type、PDF magic (`%PDF-`) と `SOURCE_MAX_PDF_BYTES` を検証し、`CONTENT_UNDERSTANDING_ENDPOINT` が設定済みなら `prebuilt-document-rag` で抽出したテキストを source draft にします。未設定または解析失敗時も、解析不可であることを示す summary をレビュー待ち source として返します。

主なエラー:

- `400 INVALID_PDF_TYPE`
- `400 INVALID_PDF_CONTENT`
- `413 PDF_TOO_LARGE`

### `POST /api/sources/audio`

raw audio はアップロードしません。短命 HTTPS `audio_url` を MAI Transcribe adapter に渡し、返った transcript だけをレビュー待ち source として保存します。`ENABLE_MAI_TRANSCRIBE_1=true`、`MAI_TRANSCRIBE_1_ENDPOINT`、`MAI_TRANSCRIBE_1_DEPLOYMENT_NAME`、`MAI_TRANSCRIBE_1_API_PATH` が揃うまで `503 AUDIO_TRANSCRIBE_UNAVAILABLE` または `501 AUDIO_TRANSCRIBE_ADAPTER_NOT_IMPLEMENTED` を返します。

```json
{
  "conversation_id": "conv-123",
  "audio_url": "https://storage.example/audio.wav?<short-lived-sas>",
  "filename": "memo.wav",
  "content_type": "audio/wav",
  "duration_seconds": 120,
  "size_bytes": 1024000,
  "language": "ja-JP"
}
```

`audio_url` は公開 payload / metadata から除去されます。`SOURCE_MAX_AUDIO_SECONDS` と `SOURCE_MAX_AUDIO_BYTES` を超える場合は transcribe 呼び出し前に `413 AUDIO_TOO_LONG` / `AUDIO_TOO_LARGE` で拒否します。

### `GET /api/sources` / `GET /api/sources/{source_id}` / `DELETE /api/sources/{source_id}`

owner scope 内の source だけを対象にします。`conversation_id` クエリで一覧を絞り込めます。別 owner の source は存在しないものとして `404 SOURCE_NOT_FOUND` を返します。

### `POST /api/sources/{source_id}/review`

```json
{
  "approved": true,
  "summary": "家族向けに自然体験と価格訴求を重視する。"
}
```

`approved=true` の場合のみレビュー済み summary がチャット文脈へ注入されます。承認後も raw text は保持しません。

## `POST /api/upload-pdf`

旧 PDF アップロード UX 互換 route です。現行実装では `data/` 配下へ保存せず、`/api/sources/pdf` と同じ source draft を返します。`ENABLE_SOURCE_INGESTION=true` が必要です。

- レート制限: 5 リクエスト / 分
- リクエストは `multipart/form-data`
- サイズ上限: `SOURCE_MAX_PDF_BYTES`（既定 10MiB、最大 25MiB）
- 拡張子と PDF ヘッダ（`%PDF-`）の両方を検証します

### リクエスト

| フィールド | 型 | 必須 | 説明 |
| --- | --- | --- | --- |
| `file` | `UploadFile` | 必須 | `.pdf` ファイル |

### レスポンス例

```json
{
  "source": {
    "id": "source-id",
    "conversation_id": "conversation-id",
    "kind": "pdf",
    "title": "existing-brochure.pdf",
    "summary": "PDF「existing-brochure.pdf」を受け取りました...",
    "status": "pending_review"
  }
}
```

### 主なエラー

- `503` `SOURCE_INGESTION_DISABLED`
- `400` `INVALID_PDF_TYPE`
- `400` `INVALID_PDF_CONTENT`
- `413` `PDF_TOO_LARGE`

## SSE 形式

全ストリームは次の形式です。

```text
event: <event-name>
data: <json>

```

## SSE イベント一覧

### `agent_progress`

```json
{
  "agent": "pipeline",
  "status": "running",
  "step": 1,
  "total_steps": 5
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `agent` | `string` | `pipeline`、`data-search-agent`、`marketing-plan-agent`、`regulation-check-agent`、`plan-revision-agent`、`brochure-gen-agent`、`video-gen-agent` のいずれか |
| `status` | `string` | `running` または `completed` |
| `step` | `int` | 現在の段階 |
| `total_steps` | `int` | 現状は 5（7 エージェントが 5 ユーザー向けステップに対応: Agent3a+3b がステップ 4、Agent4+5 がステップ 5 を共有） |

注: Azure の主フローでは `pipeline` 名で出るのが基本です。個別エージェント名はモックや修正モードで多く出ます。

### `tool_event`

```json
{
  "tool": "generate_hero_image",
  "status": "completed",
  "agent": "brochure-gen-agent"
}
```

Work IQ を有効化した新規会話では、次のような status イベントも返ります。

```json
{
  "tool": "workiq_foundry_tool",
  "status": "consent_required",
  "agent": "marketing-plan-agent",
  "source": "workiq",
  "source_scope": ["emails", "teams_chats"]
}
```

既定の `foundry_tool` 経路では `tool="workiq_foundry_tool"`、rollback の `graph_prefetch` 経路では `tool="generate_workplace_context_brief"` が使われます。フロントエンドの auth preflight では、SSE 開始前に `auth_required` / `consent_required` / `redirecting` が先に出ることがあります。`redirecting` はクライアント側でのみ発生し、ブラウザが Entra サインインへ遷移するため backend の SSE には現れません。

`foundry_tool` 経路で MCP コネクタが成功した場合、`tool_event` には次のような `source_metadata` が付きます。

```json
{
  "tool": "workiq_foundry_tool",
  "status": "completed",
  "agent": "marketing-plan-agent",
  "source": "workiq",
  "source_scope": ["meeting_notes", "emails", "teams_chats", "documents_notes"],
  "source_metadata": [
    { "source": "meeting_notes", "status": "connector_used" },
    { "source": "emails", "status": "connector_used" },
    { "source": "teams_chats", "status": "connector_used" },
    { "source": "documents_notes", "status": "connector_used" }
  ]
}
```

`status="connector_used"` は **「コネクタは正常実行されたが、Foundry MCP は per-source attribution（どのソースから何件取れたか）を expose しないため、個別の使用量は不明」** という honest なセマンティクスです。`graph_prefetch` 経路では従来通り `status="completed"` + `count` + `preview` が付きます。詳細は [`docs/sse-event-schema.md` §2.2.1](sse-event-schema.md#221-work-iq-tool_event-拡張) を参照。

改善ブリーフ MCP が設定済みで失敗した場合は、次のような fallback イベントも返ります。

```json
{
  "tool": "generate_improvement_brief",
  "status": "failed",
  "agent": "improvement-mcp",
  "source": "mcp",
  "fallback": "legacy_prompt"
}
```

主な `tool` 値:

- `generate_improvement_brief`
- `workiq_foundry_tool`
- `generate_workplace_context_brief`
- `query_data_agent`
- `search_sales_history`
- `search_customer_reviews`
- `code_interpreter`
- `web_search`
- `search_knowledge_base`
- `check_ng_expressions`
- `check_travel_law_compliance`
- `analyze_existing_brochure`
- `generate_hero_image`
- `generate_banner_image`
- `generate_promo_video`
- `source_ingestion`

Work IQ の主な `status` 値:

- `running`
- `completed`
- `auth_required`
- `identity_mismatch`
- `consent_required`
- `redirecting`（フロントエンド preflight のみ）
- `timeout`
- `unavailable`

注: Azure の主フローでも `_TOOL_EVENT_HINTS` に基づく `tool_event` が送出されます。実際に呼ばれた外部サービス数と 1:1 ではなく、UI 表示用の補助イベントです。

### `text`

```json
{
  "content": "## データ分析サマリ\n\n沖縄エリアの春季売上は前年比 **+12%** で推移。",
  "agent": "data-search-agent"
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `content` | `string` | Markdown または HTML |
| `agent` | `string` | 出力元エージェント |
| `content_type` | `string?` | HTML の場合は `html`、動画の場合は `video` |

品質レビューは `quality-review-agent` 名の追加 `text` イベントとして返ります。動画は `video-gen-agent` 名の `text` イベントで `content_type: "video"` として返ります。background update の場合は payload に `background_update: true` が付与されることがあります。

### `image`

```json
{
  "url": "data:image/png;base64,iVBORw0KGgo...",
  "alt": "沖縄の美ら海をイメージしたヒーロー画像",
  "agent": "brochure-gen-agent"
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `url` | `string` | `data:image/png;base64,...` または `data:image/svg+xml,...`。GPT 系 / MAI 画像生成失敗時は **可視 SVG プレースホルダー**（透明 PNG ではない）を返す |
| `alt` | `string` | 代替テキスト |
| `agent` | `string` | 出力元エージェント |

### `approval_request`

```json
{
  "prompt": "上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "plan_markdown": "# 春の沖縄ファミリープラン 企画書\n\n...",
  "approval_token": "ee_6D5JuLZ_4JZG0-KPq...",
  "approval_scope": "user",
  "model_settings": {"text_model": "gpt-5-4-mini", "image_model": "gpt-image-2"},
  "workflow_settings": {"manager_email": "manager@example.com", "manager_delivery_mode": "workflow"},
  "manager_approval_url": "https://app.example.com/?manager_conversation_id=...#manager_approval_token=...",
  "manager_delivery_mode": "workflow",
  "manager_comment": "上司から差し戻しされました。内容を確認して修正してください。"
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `prompt` | `string` | 承認 UI の説明文 |
| `conversation_id` | `string` | server-issued UUID4 |
| `plan_markdown` | `string` | 表示する企画書本文 |
| `approval_token` | `string` | server-issued bearer token (32 byte urlsafe)。次の `/api/chat/{id}/approve` POST で必ず echo する。`_refine_events()` で修正版を出すたびに rotation するので client は **常に最新の event の token を使う** |
| `approval_scope` | `"user"` \| `"manager"` | `manager` の場合は上司承認待ち |
| `model_settings` | `object` (optional) | client 側で記録しておく現 model 設定 |
| `workflow_settings` | `object` (optional) | manager_email / manager_delivery_mode 等 |
| `manager_approval_url` | `string` (optional) | scope=manager のときに上司に渡す URL。fragment 部に `manager_approval_token` を含む |
| `manager_delivery_mode` | `"workflow"` \| `"manual"` (optional) | Logic Apps 自動配信 vs 手動共有 |
| `manager_comment` | `string` (optional) | manager から差し戻しされた場合のコメント |

注: このイベントは Azure モードの主フローでも Agent2 完了後に送信されます。モック / デモ経路でも同様に使われます。承認 token のセキュリティモデルは [`docs/approval-security.md`](approval-security.md) を参照。

### `error`

```json
{
  "message": "パイプライン実行中にエラーが発生しました（RuntimeError）。再試行してください。",
  "code": "WORKFLOW_RUNTIME_ERROR"
}
```

主な `code` 値:

- `INPUT_GUARD_BLOCKED`
- `WORKFLOW_BUILD_ERROR`
- `WORKFLOW_RUNTIME_ERROR`
- `AGENT_RUNTIME_ERROR`
- `TOOL_RESPONSE_BLOCKED`
- `REPLAY_NOT_FOUND`

### `done`

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "background_updates_pending": true,
  "metrics": {
    "latency_seconds": 4.8,
    "tool_calls": 0,
    "total_tokens": 0
  }
}
```

`tool_calls` と `total_tokens` は承認後フローで集計され、モック経路や一部フォールバック経路では 0 のことがあります。`background_updates_pending=true` の場合、フロントエンドは会話詳細 API をポーリングし、動画・品質レビュー・承認後アクションの後続結果を同じ会話にマージします。

`ENABLE_COST_METRICS=true` かつ App Insights が設定済みの場合、`metrics.estimated_cost_usd` と `metrics.agent_estimated_costs_usd` が追加されることがあります。これは token usage からの概算で、Azure Cost Management の実課金値ではありません。`metrics.evidence` / `metrics.charts` / `metrics.source_ingestion` は安全 schema へ正規化された根拠・チャート・source ingestion 状態です。

### `evaluation_result`

> **注**: `evaluation_result` はライブ SSE ストリームでは送信されません。`done` 後にバックグラウンドで非同期に生成され、会話ドキュメント (`/api/conversations/{id}`) へ保存されます。フロントエンドは `done.background_updates_pending=true` を受けてポーリングし、`evaluation_result` を会話ドキュメントから取得します。

```json
{
  "version": 1,
  "round": 1,
  "created_at": "2026-05-04T12:34:56Z",
  "result": {
    "overall_score": 0.87,
    "criteria": {
      "plan_quality": 0.90,
      "regulation_compliance": 0.95,
      "brochure_accessibility": 0.80,
      "tone_consistency": 0.83
    },
    "comments": "キャッチコピーの語調が他セクションと微差あり"
  },
  "background_update": true
}
```

| フィールド | 型 | 説明 |
| --- | --- | --- |
| `version` | `integer` | 企画書バージョン（承認・修正ごとに increment） |
| `round` | `integer` | 評価ラウンド（同バージョンで再評価した場合 increment） |
| `created_at` | `string (ISO 8601)` | 評価完了時刻 |
| `result` | `object` | 評価スコアと各観点の詳細 |
| `background_update` | `boolean` | 常に `true`（ポーリング経由の update を示す） |

フロントエンドでは `EvaluationPanel` コンポーネントが `evaluation_result` を表示します。バージョンセレクター切替時は対応バージョンの `evaluation_result` を表示します。



### Azure モードの新規会話

```text
1. agent_progress (data-search-agent, running)
2. tool_event     (0..n)
3. text           (data-search-agent)
4. image          (0..n, Code Interpreter グラフなど)
5. agent_progress (data-search-agent, completed)
6. agent_progress (marketing-plan-agent, running)
7. tool_event     (0..n)
8. text           (marketing-plan-agent)
9. agent_progress (marketing-plan-agent, completed)
10. agent_progress (approval, running)
11. approval_request
   — user approves via POST /api/chat/{thread_id}/approve —
12. agent_progress (approval, completed)
13. agent_progress (regulation-check-agent, running)
14. tool_event / text
15. agent_progress (plan-revision-agent, running)
16. text
17. agent_progress (brochure-gen-agent, running)
18. tool_event / text(html) / image
19. agent_progress (video-gen-agent, running)
20. text           (進捗メッセージ or video URL)
21. text           (quality-review-agent, optional)
22. done
```

### モック / デモモードの新規会話

```text
1. agent_progress (data-search-agent)
2. tool_event
3. text
4. agent_progress (marketing-plan-agent)
5. tool_event
6. text
7. approval_request
```

### 承認継続

```text
1. agent_progress (regulation-check-agent)
2. text
3. agent_progress (plan-revision-agent)
4. text
5. agent_progress (brochure-gen-agent)
6. text
7. agent_progress (video-gen-agent)
8. text
9. done
```

### 評価起点の改善

```text
1. POST /api/evaluate で評価結果を取得
2. フロントエンドが改善フィードバック文を生成
3. POST /api/chat (conversation_id 付き) で改善フィードバックを送信
4. agent_progress (marketing-plan-agent, running)
5. text           (改善後の企画書)
6. agent_progress (approval, running)
7. approval_request
9. 承認後は通常の承認継続フローで下流成果物を再生成
```

補足:

- フロントエンドが生成する改善フィードバック文は、表示対象にしている評価指標だけを使います。`task_adherence` がレスポンスに存在しても、現状は改善指示へ反映しません。

## Input Guard

- 入力: `check_prompt_shield()` で明らかな指示上書きやプロンプト窃取パターンをブロックします
- ツール応答: `check_tool_response()` で外部データ中の同種パターンをブロックします

本番相当環境で `/api/ready` が `503` を返すのは、`AZURE_AI_PROJECT_ENDPOINT` が不足している場合です。

## レート制限

| エンドポイント | 制限 |
| --- | --- |
| `POST /api/chat` | 10 リクエスト / 分 |
| `POST /api/chat/{thread_id}/approve` | 10 リクエスト / 分 |
| `POST /api/evaluate` | 5 リクエスト / 分 |

## Voice Live API

### `GET /api/voice-token`

この endpoint は **廃止済み** で、常に `410 Gone` を返します。バックエンドの managed-identity token をブラウザへ返さず、`/api/voice-config` + browser delegated MSAL auth (`https://cognitiveservices.azure.com/user_impersonation`) を正規契約とします。

レスポンス例:

```json
{
  "error": "Voice token endpoint disabled",
  "code": "VOICE_TOKEN_ENDPOINT_DISABLED",
  "message": "Use /api/voice-config and browser delegated MSAL auth with https://cognitiveservices.azure.com/user_impersonation."
}
```

### `GET /api/voice-config`

Voice Live の MSAL.js クライアント設定を返します。フロントエンドはこの情報を使って MSAL.js で Entra 認証を行い、`https://cognitiveservices.azure.com/user_impersonation` の browser delegated token で Voice Live へ接続します。

レスポンス例:

```json
{
  "agent_name": "travel-voice-orchestrator",
  "client_id": "<entra-app-client-id>",
  "tenant_id": "<entra-tenant-id>",
  "resource_name": "your-foundry",
  "project_name": "your-project",
  "voice": "ja-JP-NanamiNeural",
  "vad_type": "azure_semantic_vad",
  "endpoint": "wss://your-foundry.services.ai.azure.com/voice-live/realtime",
  "api_version": "2026-01-01-preview"
}
```

`VOICE_SPA_CLIENT_ID` や `AZURE_TENANT_ID` が未設定でもこのエンドポイント自体は `200 OK` を返し、未設定項目は空文字列になります。

フロントエンドの `VoiceInput` コンポーネントは以下のフローで動作します:

1. `/api/voice-config` を呼び出して MSAL 設定を取得
2. MSAL.js で `https://cognitiveservices.azure.com/user_impersonation` スコープのトークンを取得
3. Voice Live WebSocket に接続
4. Voice Live が利用不可の場合は Web Speech API にフォールバック
