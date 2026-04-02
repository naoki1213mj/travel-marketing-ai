# API リファレンス

このドキュメントは、現在の実装を基準にした REST API と SSE の仕様です。要件書にある将来像ではなく、`src/api/chat.py`、`src/api/conversations.py`、`src/api/evaluate.py`、`src/api/voice.py`、`src/api/health.py` の現行挙動をまとめています。

## ベース URL

- ローカル: `http://localhost:8000`
- Azure: `https://<container-app-fqdn>`

## 実行モード

| モード | 条件 | 挙動 |
|---|---|---|
| Azure 本番 / Azure 接続モード | `AZURE_AI_PROJECT_ENDPOINT` が設定済み | `SequentialBuilder` を使って主フローを最後まで実行 |
| モック / デモモード | `AZURE_AI_PROJECT_ENDPOINT` 未設定 | ハードコード済みの SSE イベントを返す |
| 修正 / 改善モード | `POST /api/chat` に `conversation_id` を指定 | 承認待ち中や評価フィードバックでは `marketing-plan-agent` を再実行して新しい `approval_request` を返す。通常の修正指示はキーワードに応じて `marketing-plan-agent` / `regulation-check-agent` / `brochure-gen-agent` を再実行 |
| 承認継続モード | `POST /api/chat/{thread_id}/approve` | 承認なら Agent3a→Agent3b→Agent4→Agent5、非承認なら企画書を再生成して再度 `approval_request` を返す |

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
| `POST` | `/api/evaluate` | 品質評価の実行 |

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

## `POST /api/chat`

ユーザーメッセージを受け取り、SSE ストリームを返します。

- レート制限: 10 リクエスト / 分
- 入力は制御文字除去と軽量な注入ガードを通過したものだけが実行されます

### リクエストボディ

```json
{
  "message": "沖縄のファミリー向け春キャンペーンを企画してください",
  "conversation_id": null,
  "settings": {
    "model": "gpt-5-4-mini",
    "temperature": 0.2,
    "max_tokens": 1200,
    "top_p": 1.0,
    "iq_search_results": 5,
    "iq_score_threshold": 0.0
  }
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `message` | `string` | 必須 | 1 文字以上 |
| `conversation_id` | `string \| null` | 任意 | 既存会話 ID を指定すると修正モード |
| `settings` | `object \| null` | 任意 | フロントエンド設定パネルの内容。`model`（`gpt-5-4-mini`、`gpt-5.4`、`gpt-4-1-mini`、`gpt-4.1`）、`temperature`、`max_tokens`、`top_p`、`iq_search_results`、`iq_score_threshold` を送信できる。現行バックエンドで明示利用しているのは主に `model`、`temperature`、`max_tokens`、`top_p` |

### 現行挙動

| 条件 | SSE の主な流れ |
|---|---|
| 新規 + Azure 接続あり | `pipeline` の `agent_progress` → `text` → `approval_request` → （承認後）`text` → `done`、その後に任意で `video-gen-agent` の `text` と `quality-review-agent` の `text` |
| 新規 + Azure 接続なし | モックの各エージェント進捗と `approval_request` |
| `conversation_id` + 承認待ち中 | `marketing-plan-agent` で企画書を再生成し、新しい `approval_request` を返す |
| `conversation_id` + 完了済み + 評価フィードバック | `marketing-plan-agent` で企画書を改善し、新しい `approval_request` を返す |
| `conversation_id` + 完了済み + 通常の修正指示 | キーワードに応じて `marketing-plan-agent` / `regulation-check-agent` / `brochure-gen-agent` を再実行 |

### 注意

- Azure モードの主フローは Agent2（施策生成）完了後に `approval_request` を返し、承認後に Agent3a → Agent3b → Agent4 → Agent5 を続行します。
- `conversation_id` を指定した修正モードでも、評価フィードバック（`品質評価` または `evaluation` を含む文）は特別扱いで、企画書再生成 → 再承認フローに戻ります。
- フロントエンドは各 `done` イベントのたびに成果物スナップショットを保持し、v1 / v2 / ... を切り替えます。

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
| 承認 + Azure 接続あり | `regulation-check-agent` → `plan-revision-agent` → `brochure-gen-agent` → `video-gen-agent` を順に実行し、最後に Logic Apps callback を試行 |
| 承認 + Azure 接続なし | モックの Agent3a → Agent3b → Agent4 → Agent5 イベントを返す |
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

## `POST /api/evaluate`

企画書とブローシャを評価し、Built-in 指標、カスタム指標、LLM ジャッジ結果をまとめて返します。

- レート制限: 5 リクエスト / 分
- フロントエンドでは企画書タブの評価パネルから呼ばれます
- `AZURE_AI_PROJECT_ENDPOINT` が未設定でも呼び出せますが、Built-in 評価と prompt-based 評価はエラー / 低機能モードになります

### リクエストボディ

```json
{
  "query": "沖縄のファミリー向け春キャンペーンを企画してください",
  "response": "# 春の沖縄ファミリープラン\n\n...",
  "html": "<!DOCTYPE html><html lang=\"ja\">...</html>"
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `query` | `string` | 必須 | 元のユーザー依頼 |
| `response` | `string` | 必須 | 企画書 Markdown |
| `html` | `string` | 任意 | ブローシャ HTML。空文字可。未指定時のコンバージョン期待度判定は `response` を代用 |

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
  "foundry_portal_url": "https://ai.azure.com/..."
}
```

| フィールド | 型 | 説明 |
|---|---|---|
| `custom` | `object` | code-based カスタム評価。現在は `travel_law_compliance` と `conversion_potential` |
| `builtin` | `object` | `azure-ai-evaluation` による `relevance` / `coherence` / `fluency` |
| `marketing_quality` | `object` | prompt-based LLM ジャッジ。`appeal` / `differentiation` / `kpi_validity` / `brand_tone` / `overall` |
| `foundry_portal_url` | `string?` | Foundry への評価ログに成功した場合のみ返るポータル URL |

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

主な `tool` 値:

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

注: Azure の主フローでも `_TOOL_EVENT_HINTS` に基づく `tool_event` が送出されます。実際に呼ばれた外部サービス数と 1:1 ではなく、UI 表示用の補助イベントです。

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
| `content_type` | `string?` | HTML の場合は `html`、動画の場合は `video` |

品質レビューは `quality-review-agent` 名の追加 `text` イベントとして返ります。動画は `video-gen-agent` 名の `text` イベントで `content_type: "video"` として返ります。

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
  "metrics": {
    "latency_seconds": 4.8,
    "tool_calls": 0,
    "total_tokens": 0
  }
}
```

`tool_calls` と `total_tokens` は承認後フローで集計され、モック経路や一部フォールバック経路では 0 のことがあります。

## 代表的な SSE フロー

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

## Input Guard

- 入力: `check_prompt_shield()` で明らかな指示上書きやプロンプト窃取パターンをブロックします
- ツール応答: `check_tool_response()` で外部データ中の同種パターンをブロックします

本番相当環境で `/api/ready` が `503` を返すのは、`AZURE_AI_PROJECT_ENDPOINT` が不足している場合です。

## レート制限

| エンドポイント | 制限 |
|---|---|
| `POST /api/chat` | 10 リクエスト / 分 |
| `POST /api/chat/{thread_id}/approve` | 10 リクエスト / 分 |
| `POST /api/evaluate` | 5 リクエスト / 分 |

## Voice Live API

### `GET /api/voice-token`

Voice Live 接続用の AAD トークンと設定を返します。`AZURE_AI_PROJECT_ENDPOINT` から `resource_name` と `project_name` を導出し、`DefaultAzureCredential` で `https://ai.azure.com/.default` スコープのトークンを取得します。

レスポンス例:

```json
{
  "token": "<aad-token>",
  "expires_on": 1767225600,
  "resource_name": "your-foundry",
  "project_name": "your-project",
  "endpoint": "wss://your-foundry.services.ai.azure.com/voice-live/realtime",
  "api_version": "2026-01-01-preview"
}
```

失敗時:

```json
{"error": "Voice token unavailable"}
```

### `GET /api/voice-config`

Voice Live の MSAL.js クライアント設定を返します。フロントエンドが MSAL.js で Entra 認証を行うために使用します。

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
