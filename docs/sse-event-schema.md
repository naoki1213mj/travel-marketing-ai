# SSE イベントスキーマリファレンス

`GET /api/chat` のレスポンスは `text/event-stream` 形式の SSE ストリームです。
各イベントは `data: <JSON>\n\n` 形式で送信されます。
フロントエンドの `SSEEventType` 型定義 (`frontend/src/lib/sse-client.ts`) と、
イベント正規化ロジック (`frontend/src/hooks/useSSE.ts`) が規範的実装です。

## 1. イベントタイプ列挙

```typescript
// frontend/src/lib/sse-client.ts
export type SSEEventType =
  | 'agent_progress'
  | 'tool_event'
  | 'text'
  | 'image'
  | 'approval_request'
  | 'error'
  | 'done'
```

> **`evaluation_result`** はライブ SSE には含まれません。
> `done` 後にバックグラウンドで非同期生成され、会話ドキュメント
> (`/api/conversations/{id}`) へ書き込まれます（§6 参照）。

## 2. イベント別スキーマ

### 2.1 `agent_progress`

エージェントの開始・完了を通知します。フロントエンドはステップインジケーターと進捗バーに使用します。

**バックエンド**: `src/api/chat.py` → `_emit_agent_progress()`

```typescript
interface AgentProgressEvent {
  type: 'agent_progress';
  agent: string;          // エージェント識別子
  status: 'running' | 'completed';
  step: number;           // 1-indexed
  total_steps: number;
}
```

**agent フィールドの値と対応エージェント**:

| `agent` 値 | 対応エージェント |
| --- | --- |
| `data-search-agent` | Agent1: データ検索 |
| `marketing-plan-agent` | Agent2: 施策生成 |
| `approval` | 承認ステップ（仮想） |
| `regulation-check-agent` | Agent3a: 規制チェック |
| `plan-revision-agent` | Agent3b: 企画書修正 |
| `brochure-gen-agent` | Agent4: 販促物生成 |
| `video-gen-agent` | Agent5: 動画生成 |
| `quality-review-agent` | Agent6: 品質レビュー（オプション） |

---

### 2.2 `tool_event`

ツール呼び出しの開始・完了を通知します。フロントエンドは `ToolEventCard` コンポーネントでバッジ付き表示します。

**バックエンド**: `src/api/chat.py` → `_emit_tool_event()`

```typescript
interface ToolEvent {
  type: 'tool_event';
  tool: string;           // ツール識別子
  status:
    | 'running'
    | 'completed'
    | 'error'
    | 'failed'
    | 'auth_required'
    | 'consent_required'
    | 'identity_mismatch'
    | 'unavailable'
    | 'timeout'
    | 'skipped'
    | 'partial';
  agent: string;          // 呼び出し元エージェント
  source?: string;        // データソース種別（例: "fabric_da_v2", "search", "iq", "workiq"）
  source_scope?: string[];  // スコープ詳細（Work IQ では選択ソース配列、その他は単一値の配列）
  source_metadata?: WorkIqSourceMetadata[];  // §2.2.1 を参照（Work IQ 系のみ）
  version?: number;       // 企画書バージョン
  background_update?: boolean;  // バックグラウンド更新の場合 true
  // ツール固有の追加フィールド（任意）
  [key: string]: unknown;
}
```

> **status の使われ方**: `running` / `completed` / `error` が大半の経路で使われます。
> `failed` / `auth_required` / `consent_required` / `identity_mismatch` / `unavailable` /
> `timeout` / `skipped` / `partial` は Work IQ MCP 連携・規制チェック・動画生成など特定の経路で使われます。

**source フィールドの値と対応ツール**:

| `source` 値 | 対応ツール / サービス |
| --- | --- |
| `fabric_da_v2` | Fabric Data Agent v2 (MicrosoftFabricPreviewTool) |
| `fabric_sql` | Fabric Lakehouse SQL endpoint |
| `search` | Bing grounding / Web Search |
| `iq` | Foundry IQ Knowledge Base |
| `mcp` | MCP サーバー (Azure Functions) |
| `workiq` | Work IQ MCP connector (M365 データ) |
| `code_interpreter` | Code Interpreter |
| `image` | 画像生成モデル |
| `avatar` | Photo Avatar API |

---

### 2.2.1 Work IQ tool_event 拡張

Agent2 (`marketing-plan-agent`) で Work IQ が有効化されている場合、`tool_event` には
追加で `source_metadata` 配列が付きます。`source_metadata` 各 item の schema は
`src/pipeline_schemas.py` の `WorkIQSourceMetadata` model と
`frontend/src/lib/event-schemas.ts` の `WorkIqSourceMetadata` 型に対応します。

```typescript
interface WorkIqSourceMetadata {
  source: 'meeting_notes' | 'emails' | 'teams_chats' | 'documents_notes';
  label?: string;        // ローカライズ済みラベル（フロント側で補完される場合あり）
  count?: number;        // 取得件数（graph_prefetch のみ。foundry_tool では未提供）
  connector?: string;    // 上流コネクタ識別子
  status?:
    | 'completed'
    | 'connector_used'   // foundry_tool 用: コネクタは実行されたが per-source attribution 不明
    | 'auth_required'
    | 'consent_required'
    | 'unavailable'
    | 'failed'
    | 'pending';
  summary?: string;      // sanitized 要約（公開しても安全な短文）
  preview?: string;      // sanitized プレビュー
  confidence?: number;   // 0.0–1.0
  latest_timestamp?: string;
  evidence_ids?: string[];
}
```

**`connector_used` セマンティクス**: Foundry MCP は per-source attribution
（どのソースから何件取れたか）を expose しないため、`foundry_tool` runtime では
コネクタ実行成功後に `selected_sources` 全件を `status="connector_used"` でマークします。
`count` / `preview` / `summary` は付かず、UI 側 (`WorkIqSourceStatus.tsx`) では sky tone
の「コネクタ実行」バッジで表示されます。`graph_prefetch` runtime では従来通り
`status="completed"` + `count` + `preview` が付きます。

> **UI 表示ルール**: `sourceMetadata` が全件 `connector_used` で
> `count` / `preview` / `summary` / `briefSummary` も無い場合、
> `WorkIqSourceStatus` コンポーネントは描画自体を省略します
> （ランタイム表示と「この会話で有効」バッジで Work IQ アクティブ状態は伝わるため）。

---

### 2.3 `text`

エージェントが生成したテキストコンテンツ（Markdown または HTML）を送信します。
フロントエンドは `content_type` に応じて `AgentTextContent`（Markdown）または HTML プレビューを表示します。

**バックエンド**: `src/api/chat.py` → `_emit_text()`

```typescript
interface TextEvent {
  type: 'text';
  content: string;                  // Markdown または HTML
  agent: string;
  content_type?: 'markdown' | 'html';  // 省略時は 'markdown'
  evidence?: EvidenceItem[];        // 根拠データ（Fabric DA 検索結果等）
  charts?: ChartData[];             // Code Interpreter 生成チャート
  trace_events?: TraceEvent[];      // デバッグ用トレース
  debug_events?: DebugEvent[];      // デバッグ用イベント
  source_metadata?: SourceMeta;     // ソース情報メタデータ
  source_ingestion?: IngestionState; // ソース取り込み状態
  background_update?: boolean;      // バックグラウンド更新の場合 true
  version?: number;                 // 企画書バージョン
}
```

> **HTML ブローシャ**: Agent4 (brochure-gen-agent) が送信する `text` イベントは
> `content_type: 'html'` で Tailwind CSS 入りの顧客向けブローシャ HTML を送信します。
> ブローシャには KPI / 売上目標 / 社内分析などの社内情報を含めません。

---

### 2.4 `image`

画像データ（Base64 data URI または URL）を送信します。
フロントエンドは `ImageGallery` コンポーネントでヒーロー画像・SNS バナーを表示します。

**バックエンド**: `src/api/chat.py` → `_emit_image()`

```typescript
interface ImageEvent {
  type: 'image';
  url: string;                // Base64 data URI ("data:image/png;base64,...") または HTTPS URL
  alt: string;                // alt テキスト（アクセシビリティ用）
  agent: string;
  evidence?: EvidenceItem[];
  charts?: ChartData[];
  trace_events?: TraceEvent[];
  debug_events?: DebugEvent[];
  source_metadata?: SourceMeta;
  source_ingestion?: IngestionState;
  background_update?: boolean;
  version?: number;
}
```

> **フォールバック**: 画像生成 API (GPT Image 2 / GPT Image 1.5 / MAI-Image-2) が
> 失敗した場合は透明 PNG ではなく可視 SVG プレースホルダーを `url` に設定します。
> `background_update: true` の場合、`done` 後に動画 URL が会話ドキュメント経由で届きます。

---

### 2.5 `approval_request`

ユーザーへ企画書承認を要求します。フロントエンドは `ApprovalPanel` コンポーネントで
Markdown プレビュー付きの承認 UI を表示します。

**バックエンド**: `src/api/chat.py` → `_emit_approval_request()`

```typescript
interface ApprovalRequestEvent {
  type: 'approval_request';
  prompt: string;                           // 承認依頼メッセージ
  conversation_id: string;
  plan_markdown?: string;                   // 承認対象の企画書 Markdown
  approval_scope?: 'user' | 'manager';     // 'user': ユーザー直接承認, 'manager': 上長承認
  manager_email?: string;                  // manager 承認時の宛先
  manager_comment?: string;               // 上長へのコメント
  manager_approval_url?: string;          // Logic Apps 生成の承認 URL
  manager_delivery_mode?: string;         // 通知方法 (例: "teams", "email")
  approval_token?: string;                // 承認 API 呼び出し用トークン（32-byte urlsafe）
}
```

> **セキュリティ**: `approval_token` は per-conversation で `secrets.token_urlsafe(32)` で
> 生成され、`POST /api/chat/{id}/approve` の呼び出し時に必須です。
> `hmac.compare_digest()` で定数時間比較されます。詳細は `docs/approval-security.md` を参照。

---

### 2.6 `error`

エラー情報を送信します。フロントエンドは `ErrorBanner` コンポーネントで表示します。
必要に応じて Azure AD / Entra の同意リンクを含みます。

**バックエンド**: `src/api/chat.py` → `_emit_error()`

```typescript
interface ErrorEvent {
  type: 'error';
  message: string;             // ユーザー向けエラーメッセージ
  code: string;                // エラーコード（例: "APPROVAL_CONTEXT_NOT_FOUND"）
  consent_link?: string;       // Azure AD 同意 URL（正規化名）
  consentLink?: string;        // 後方互換エイリアス
  auth_link?: string;          // 認証 URL（正規化名）
  authLink?: string;           // 後方互換エイリアス
}
```

**主要エラーコード**:

| `code` | 説明 |
| --- | --- |
| `APPROVAL_CONTEXT_NOT_FOUND` | approval_token 不在または不一致 |
| `CONVERSATION_NOT_FOUND` | 会話 ID が見つからない |
| `FABRIC_DA_UNAVAILABLE` | Fabric Data Agent v2 が応答しない（Pass 2 に降格） |
| `AUTH_REQUIRED` | Azure AD 認証が必要（`auth_link` を参照） |
| `CONSENT_REQUIRED` | スコープ同意が必要（`consent_link` を参照） |
| `RATE_LIMIT` | レートリミット（429）。自動 retry/backoff 後も失敗 |

---

### 2.7 `done`

ライブ SSE ストリームの終了を示します。フロントエンドはバージョンスナップショットを
保存し、バージョンセレクターを有効化します。

**バックエンド**: `src/api/chat.py` → `_emit_done()`

```typescript
interface DoneEvent {
  type: 'done';
  conversation_id?: string;
  background_updates_pending?: boolean;  // true なら会話ドキュメントをポーリング
  metrics?: {
    latency_seconds: number;
    tool_calls: number;
    total_tokens: number;
    estimated_cost_usd?: number;         // ENABLE_COST_METRICS=true のときのみ
    agent_estimated_costs_usd?: Record<string, number>;
    evidence?: EvidenceItem[];
    charts?: ChartData[];
    source_ingestion?: IngestionState;
  };
}
```

`background_updates_pending: true` の場合、フロントエンドは
`/api/conversations/{id}` をポーリングし、動画生成・品質レビュー・`evaluation_result` を
同じ会話にマージします。

---

## 3. バックグラウンド更新パターン

`done` イベント受信後もバックグラウンドで非同期処理が続く場合があります。
フロントエンドは `done.background_updates_pending=true` の場合にポーリングを開始します。

| 処理 | 配信方法 | 対応コンポーネント |
| --- | --- | --- |
| 動画生成 (Agent5) | `image` イベント (`background_update: true`) または会話ドキュメント `video_url` | `ImageGallery` |
| 品質レビュー (Agent6) | `text` イベント (`background_update: true`) または会話ドキュメント | `AgentTextContent` |
| 評価結果 | 会話ドキュメント `evaluation_result` フィールド（ライブ SSE 外） | `EvaluationPanel` |

### `evaluation_result`（バックグラウンド専用）

```typescript
interface EvaluationResult {
  version: number;        // 企画書バージョン
  round: number;          // 同バージョンでの評価ラウンド
  created_at: string;     // ISO 8601
  result: {
    overall_score: number;  // 0.0–1.0
    criteria: {
      plan_quality: number;
      regulation_compliance: number;
      brochure_accessibility: number;
      tone_consistency: number;
    };
    comments: string;
  };
  background_update: true;
}
```

---

## 4. ツールイベント サブタイプ詳細

`tool_event` の `tool` フィールドは呼び出しツール名を示します。主要な値は以下の通りです:

| `tool` 値 | エージェント | 説明 |
| --- | --- | --- |
| `query_data_agent` | Agent1 | Fabric Data Agent 呼び出し（v1/v2、Foundry path では `MicrosoftFabricPreviewTool` 経由）。`source="fabric_da_v2"` |
| `search_sales_history` | Agent1 | Fabric Lakehouse 売上履歴検索（Pass 2 fallback） |
| `search_customer_reviews` | Agent1 | Fabric Lakehouse 顧客レビュー検索（Pass 2 fallback） |
| `code_interpreter` | Agent1 | Foundry built-in Code Interpreter（`ENABLE_CODE_INTERPRETER=true` のときのみ） |
| `web_search` | Agent2 / Agent3a | Bing grounding / Web Search（マーケットトレンド・安全情報の双方で使用） |
| `workiq_foundry_tool` | Agent2 | 既定 `foundry_tool` runtime: 事前作成済み Foundry Prompt Agent + Work IQ MCP connection |
| `generate_workplace_context_brief` | Agent2 | rollback `graph_prefetch` runtime: Microsoft Graph Copilot Chat API |
| `foundry_iq_search` | Agent3a | Foundry IQ ナレッジベース検索（実装上の関数名は `search_knowledge_base`、テレメトリでは alias として `foundry_iq_search` に正規化） |
| `check_ng_expressions` | Agent3a | 禁止表現スキャン（ローカル処理） |
| `check_travel_law_compliance` | Agent3a | 旅行業法チェック（ローカル処理） |
| `generate_hero_image` | Agent4 | ヒーロー画像生成（GPT Image 2 / 1.5 / MAI-Image-2） |
| `generate_banner_image` | Agent4 | SNS バナー画像生成 |
| `analyze_existing_brochure` | Agent4 | Content Understanding による既存パンフレット PDF 解析 |
| `generate_promo_video` | Agent5 | Photo Avatar 販促動画生成 |
| `review_plan_quality` | Agent6 | 企画書品質レビュー（ローカル処理） |
| `review_brochure_accessibility` | Agent6 | ブローシャアクセシビリティチェック（ローカル処理） |
| `generate_improvement_brief` | Agent2 (refine) | APIM 配下の Functions MCP 改善ブリーフ生成 |

---

## 5. フロントエンド コンポーネントマッピング

| イベントタイプ | フロントエンドコンポーネント | ファイル |
| --- | --- | --- |
| `agent_progress` | ステップインジケーター / プログレスバー | `frontend/src/components/` |
| `text` (markdown) | `AgentTextContent` | `frontend/src/components/AgentTextContent.tsx` |
| `text` (html) | HTML プレビュー（ブローシャ） | `frontend/src/components/` |
| `image` | `ImageGallery` / ヒーロー画像プレビュー | `frontend/src/components/ImageGallery.tsx` |
| `tool_event` | `ToolEventCard` | `frontend/src/components/ToolEventCard.tsx` |
| `approval_request` | `ApprovalPanel` | `frontend/src/components/ApprovalPanel.tsx` |
| `error` | `ErrorBanner` | `frontend/src/components/ErrorBanner.tsx` |
| `done` | バージョンセレクター有効化 | `frontend/src/hooks/useSSE.ts` |
| `evaluation_result` | `EvaluationPanel` | `frontend/src/components/EvaluationPanel.tsx` |

---

## 6. 会話復元時のイベント再生

会話ドキュメント (`/api/conversations/{id}`) から過去の会話を復元する場合、
フロントエンドの `buildRestoredPipelineState()` 関数（`useSSE.ts`）が
保存済みイベントを switch-case で処理します。

復元対象のイベントタイプ（ライブ SSE とほぼ同じ、`evaluation_result` が追加）:
- `agent_progress`, `tool_event`, `text`, `image`, `approval_request`, `error`, `done`
- `evaluation_result`（ライブ SSE にはない、ポーリング経由で追記されたもの）

---

## 参照

- `frontend/src/lib/sse-client.ts` — `SSEEventType` 型定義
- `frontend/src/hooks/useSSE.ts` — イベント正規化・状態更新ロジック
- `src/api/chat.py` — バックエンド `_emit_*` 関数群
- `docs/api-reference.md` — `/api/chat` エンドポイント仕様
- `docs/approval-security.md` — `approval_token` セキュリティ詳細
