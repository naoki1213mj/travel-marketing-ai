---
name: 'Python FastAPI ルール'
description: 'バックエンド Python コードの規約'
applyTo: 'src/**/*.py, tests/**/*.py'
---

## Python / FastAPI 規約

### Agent Framework（rc5 (1.0.0rc5) 準拠）

- クライアント: `AzureOpenAIResponsesClient(project_endpoint=..., credential=DefaultAzureCredential())`
- エージェント作成: `client.as_agent(name=..., tools=..., middleware=...)`
- ツール定義: `@tool` デコレータ（`@ai_function` は削除済み）
- 実行: `await agent.run("文字列")`（Message オブジェクト不要）
- Workflow: `SequentialBuilder(participants=[...]).build()`
- エンドポイント環境変数: `AZURE_AI_PROJECT_ENDPOINT`
- middleware の `call_next` は引数なし: `await call_next()`
- 設定は `TypedDict + load_settings()`（Pydantic Settings は廃止）

### FastAPI

- ルーターは `src/api/` 配下に配置。`main.py` で `include_router` する
- SSE ストリーミングは `StreamingResponse(media_type="text/event-stream")`
- SSE イベント形式: `event: {type}\ndata: {json}\n\n`
- イベント種別: `agent_progress`, `tool_event`, `text`, `image`, `approval_request`, `safety`, `error`, `done`
- エラーハンドリング: 具体的な例外型で catch。bare except 禁止
- Content Safety チェック: `/api/chat` のエンドポイントで入力時に Prompt Shield を実行

### パッケージ管理

- `uv add <package>` で追加。pip install は使わない
- プレリリース: `uv add agent-framework --prerelease=allow`
- テスト実行: `uv run pytest`
- リント: `uv run ruff check .`

### 型ヒント

- 必須。`str | None` 形式
- Pydantic モデルでリクエスト/レスポンスを定義する
- TypeVar より具体的な型を使う
