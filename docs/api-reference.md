# API リファレンス

このドキュメントは、現在の実装を基準にした REST API と SSE の仕様です。要件書にある将来像ではなく、`src/api/chat.py`、`src/api/conversations.py`、`src/api/health.py` の現行挙動をまとめています。

## ベース URL

- ローカル: `http://localhost:8000`
- Azure: `https://<container-app-fqdn>`

## 実行モード

| モード | 条件 | 挙動 |
|---|---|---|
| Azure 本番 / Azure 接続モード | `AZURE_AI_PROJECT_ENDPOINT` が設定済み | `SequentialBuilder` を使って主フローを最後まで実行 |
| モック / デモモード | `AZURE_AI_PROJECT_ENDPOINT` 未設定 | ハードコード済みの SSE イベントを返す |
| 修正モード | `POST /api/chat` に `conversation_id` を指定 | キーワードに応じて単一エージェントを再実行 |
| 承認継続モード | `POST /api/chat/{thread_id}/approve` | 承認なら Agent3→Agent4、修正なら再調整経路 |

## エンドポイント一覧

| メソッド | パス | 説明 |
|---|---|---|
| `GET` | `/api/health` | ライブネスプローブ |
| `GET` | `/api/ready` | 本番必須設定の readiness チェック |
| `POST` | `/api/chat` | メインチャット SSE |
| `POST` | `/api/chat/{thread_id}/approve` | 承認継続または修正継続 |
| `GET` | `/api/conversations` | 会話一覧取得 |
| `GET` | `/api/conversations/{conversation_id}` | 会話詳細取得（履歴復元用） |
| `GET` | `/api/replay/{conversation_id}` | 保存済み SSE リプレイ |
| `GET` | `/api/voice-token` | Voice Live 用 AAD トークン取得 |
| `GET` | `/api/voice-config` | Voice Live MSAL 設定取得 |

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
  "missing": ["AZURE_AI_PROJECT_ENDPOINT", "CONTENT_SAFETY_ENDPOINT"]
}
```

## `POST /api/chat`

ユーザーメッセージを受け取り、SSE ストリームを返します。

- レート制限: 10 リクエスト / 分
- 入力は制御文字除去と Prompt Shield チェックを通過したものだけが実行されます

### リクエストボディ

```json
{
  "message": "沖縄のファミリー向け春キャンペーンを企画してください",
  "conversation_id": null,
  "settings": {
    "temperature": 0.2,
    "max_tokens": 1200
  }
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `message` | `string` | 必須 | 1〜5000 文字 |
| `conversation_id` | `string \| null` | 任意 | 既存会話 ID を指定すると修正モード |
| `settings` | `object \| null` | 任意 | `model` でテキスト推論モデルを選択可能（`gpt-5-4-mini`、`gpt-5.4`、`gpt-4-1-mini`、`gpt-4.1`）。フロントエンドのモデルセレクターから送信される。`temperature`、`max_tokens` も将来拡張用に受け付ける |

### 現行挙動

| 条件 | SSE の主な流れ |
|---|---|
| 新規 + Azure 接続あり | `pipeline` の `agent_progress` → `text` → `approval_request` → （承認後）`text` → `safety` → `done`、その後に任意で `quality-review-agent` の `text` |
| 新規 + Azure 接続なし | モックの各エージェント進捗と `approval_request` |
| `conversation_id` あり | 指示内容に応じて `marketing-plan-agent` / `regulation-check-agent` / `brochure-gen-agent` を再実行 |

### 注意

- Azure モードの主フローは Agent2（施策生成）完了後に `approval_request` を返し、承認後に Agent3 → Agent4 を続行します。
- `conversation_id` を指定した修正モードでは、会話履歴全体を再構成するのではなく、対象エージェントを個別に呼び直します。

### cURL 例

```bash
curl -N -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"沖縄のファミリー向け春キャンペーンを企画してください"}'
```

## `POST /api/chat/{thread_id}/approve`

承認継続または修正継続のための SSE エンドポイントです。

- レート制限: 10 リクエスト / 分
- `response` も Prompt Shield チェック対象です
- パスの `thread_id` が実際に使われる会話 ID です

### リクエストボディ

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "response": "承認"
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `conversation_id` | `string` | 必須 | バリデーション用に受け取る互換フィールド。現状は処理で未使用 |
| `response` | `string` | 必須 | 承認キーワードまたは修正指示 |

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

### 現行挙動

| 条件 | 挙動 |
|---|---|
| 承認 + Azure 接続あり | `regulation-check-agent` → `brochure-gen-agent` を順に実行し、最後に Logic Apps callback を試行 |
| 承認 + Azure 接続なし | モックの Agent3 → Agent4 イベントを返す |
| 非承認 | 修正テキストとして扱い、再調整経路に入る |

## 会話 API

### `GET /api/conversations`

会話一覧を返します。Cosmos DB が未設定ならインメモリから返します。

クエリ:

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
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
  "user_id": "demo-user",
  "created_at": "2026-03-20T10:30:00+00:00",
  "updated_at": "2026-03-20T10:31:10+00:00",
  "status": "completed",
  "input": "沖縄のファミリー向け春キャンペーン",
  "messages": [],
  "artifacts": {},
  "metadata": {}
}
```

未存在時:

```json
{"error": "conversation not found"}
```

### `GET /api/replay/{conversation_id}`

保存済み SSE を `speed` 倍速でリプレイします。

| パラメータ | 型 | デフォルト | 説明 |
|---|---|---|---|
| `speed` | `float` | `5.0` | リプレイ倍率 |

データがない場合は `error` イベントで以下を返します。

```json
{
  "message": "リプレイデータが見つかりません",
  "code": "REPLAY_NOT_FOUND"
}
```

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
|---|---|---|
| `agent` | `string` | `pipeline`、`data-search-agent`、`marketing-plan-agent`、`regulation-check-agent`、`brochure-gen-agent` のいずれか |
| `status` | `string` | `running` または `completed` |
| `step` | `int` | 現在の段階 |
| `total_steps` | `int` | 現状は 5（4 エージェント + 1 承認ステップ） |

注: Azure の主フローでは `pipeline` 名で出るのが基本です。個別エージェント名はモックや修正モードで多く出ます。

### `tool_event`

```json
{
  "tool": "generate_hero_image",
  "status": "completed",
  "agent": "brochure-gen-agent"
}
```

主な `tool` 値:

- `search_sales_history`
- `search_customer_reviews`
- `web_search`
- `search_knowledge_base`
- `check_ng_expressions`
- `check_travel_law_compliance`
- `generate_hero_image`
- `generate_banner_image`

注: Azure の `workflow_event_generator()` では現在 `tool_event` は個別には流れず、モック経路や単一エージェント再実行で主に見えます。

### `text`

```json
{
  "content": "## データ分析サマリ\n\n沖縄エリアの春季売上は前年比 **+12%** で推移。",
  "agent": "data-search-agent"
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `content` | `string` | Markdown または HTML |
| `agent` | `string` | 出力元エージェント |
| `content_type` | `string?` | HTML の場合は `html` |

品質レビューは `quality-review-agent` 名の追加 `text` イベントとして返ります。

### `image`

```json
{
  "url": "data:image/png;base64,iVBORw0KGgo...",
  "alt": "沖縄の美ら海をイメージしたヒーロー画像",
  "agent": "brochure-gen-agent"
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `url` | `string` | `data:image/png;base64,...` または `data:image/svg+xml,...` |
| `alt` | `string` | 代替テキスト |
| `agent` | `string` | 出力元エージェント |

### `approval_request`

```json
{
  "prompt": "上記の企画書を確認してください。承認する場合は「承認」、修正したい場合は修正内容を入力してください。",
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "plan_markdown": "# 春の沖縄ファミリープラン 企画書\n\n..."
}
```

注: このイベントは Azure モードの主フローでも Agent2 完了後に送信されます。モック / デモ経路でも同様に使われます。

### `safety`

```json
{
  "hate": 0,
  "self_harm": 0,
  "sexual": 0,
  "violence": 0,
  "status": "safe"
}
```

`status` は `safe`、`warning`、`error` のいずれかです。

### `error`

```json
{
  "message": "パイプライン実行中にエラーが発生しました（RuntimeError）。再試行してください。",
  "code": "WORKFLOW_RUNTIME_ERROR"
}
```

主な `code` 値:

- `PROMPT_SHIELD_BLOCKED`
- `WORKFLOW_BUILD_ERROR`
- `WORKFLOW_RUNTIME_ERROR`
- `AGENT_RUNTIME_ERROR`
- `TOOL_RESPONSE_BLOCKED`
- `REPLAY_NOT_FOUND`

### `done`

```json
{
  "conversation_id": "550e8400-e29b-41d4-a716-446655440000",
  "metrics": {
    "latency_seconds": 4.8,
    "tool_calls": 0,
    "total_tokens": 0
  }
}
```

`tool_calls` と `total_tokens` は現状の Azure 主フローでは 0 のまま返ることがあります。

## 代表的な SSE フロー

### Azure モードの新規会話

```text
1. agent_progress (pipeline, running)
2. text           (pipeline — Agent1 + Agent2 results)
3. approval_request
   — user approves via POST /api/chat/{thread_id}/approve —
4. agent_progress (regulation-check-agent)
5. text           (regulation-check-agent)
6. agent_progress (brochure-gen-agent)
7. text           (brochure-gen-agent)
8. safety
9. done
10. text           (quality-review-agent, optional)
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
3. safety
4. done
5. agent_progress (brochure-gen-agent)
6. text
7. safety
8. done
```

## Content Safety

- 入力: `check_prompt_shield()` で Prompt Shield を実行
- ツール応答: `check_tool_response()` を実行
- 出力: `analyze_content()` で Text Analysis を実行

本番相当環境では `CONTENT_SAFETY_ENDPOINT` が不足すると `/api/ready` は `503` を返します。

## レート制限

| エンドポイント | 制限 |
|---|---|
| `POST /api/chat` | 10 リクエスト / 分 |
| `POST /api/chat/{thread_id}/approve` | 10 リクエスト / 分 |

## Voice Live API

### `GET /api/voice-token`

Voice Live 接続用の AAD トークンと設定を返します。`SPEECH_SERVICE_ENDPOINT` が設定されている場合のみ有効です。

レスポンス例:

```json
{
  "token": "<aad-token>",
  "endpoint": "https://<speech-endpoint>",
  "resource_name": "<resource-name>",
  "api_version": "2024-11-15"
}
```

未設定時:

```json
{"error": "Speech service not configured"}
```

### `GET /api/voice-config`

Voice Live の MSAL.js クライアント設定を返します。フロントエンドが MSAL.js で Entra 認証を行うために使用します。

レスポンス例:

```json
{
  "client_id": "<entra-app-client-id>",
  "tenant_id": "<entra-tenant-id>",
  "agent_name": "travel-marketing-agent",
  "endpoint": "https://<speech-endpoint>"
}
```

未設定時:

```json
{"error": "Voice Live not configured"}
```

フロントエンドの `VoiceInput` コンポーネントは以下のフローで動作します:
1. `/api/voice-config` を呼び出して MSAL 設定を取得
2. MSAL.js で `https://cognitiveservices.azure.com/user_impersonation` スコープのトークンを取得
3. Voice Live WebSocket に接続
4. Voice Live が利用不可の場合は Web Speech API にフォールバック
