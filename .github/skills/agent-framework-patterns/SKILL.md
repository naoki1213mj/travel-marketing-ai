---
name: agent-framework-patterns
description: >-
  Microsoft Agent Framework (Python) GA 1.0.0 のコードパターン集。
  エージェント作成、ツール定義、Workflow 構築、middleware、Hosted Agent デプロイの正しい書き方を提供する。
  Triggers: "agent-framework", "Agent Framework", "エージェント作成", "ツール定義", "@tool",
  "Workflow", "SequentialBuilder", "middleware", "FoundryChatClient"
---

# Microsoft Agent Framework パターン集（GA 1.0.0 準拠）

## インストール

```bash
# GA 版は通常インストール（--prerelease 不要）
uv add agent-framework-core==1.0.0 agent-framework-foundry==1.0.0

# beta connector（Fabric Data Agent プレビューツール等）だけ prerelease を許容
uv add agent-framework-fabric-connector --prerelease=allow
```

## エージェント作成

```python
from azure.identity import DefaultAzureCredential
from agent_framework.foundry import FoundryChatClient

# クライアント作成
client = FoundryChatClient(
    project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    model=os.environ["MODEL_NAME"],
    credential=DefaultAzureCredential(),
)

# エージェント作成
agent = client.as_agent(
    name="data-search-agent",
    instructions="あなたは旅行データの分析エージェントです。",
    tools=[search_sales_history, analyze_trends],
)

# 実行
result = await agent.run("沖縄の夏季売上データを分析して")
```

## ツール定義

```python
from agent_framework import tool

@tool
async def search_sales_history(
    query: str,
    season: str | None = None,
    region: str | None = None,
) -> str:
    """Fabric Lakehouse の sales_history を SQL EP 経由で検索する。

    Args:
        query: 検索クエリ
        season: 季節フィルタ（spring/summer/autumn/winter）
        region: 地域フィルタ
    """
    # SQL EP 経由でクエリ実行
    ...
```

## Sequential Workflow（7 エージェントパイプライン）

> **注**: `SequentialBuilder` は HITL（Human-in-the-Loop 承認中断）をサポートしない。
> 承認フローが必要な場合は FastAPI 側で Agent1→Agent2→承認→Agent3a→Agent3b→Agent4→Agent5 を
> 明示実行する（`src/api/chat.py` の `workflow_event_generator()` を参照）。

```python
from agent_framework.orchestrations import SequentialBuilder

# HITL なしの連続実行が必要な場合（Agent6 品質レビュー等）
workflow = SequentialBuilder(
    participants=[
        data_search_agent,     # Agent1: Fabric Lakehouse + Code Interpreter
        marketing_plan_agent,  # Agent2: 施策生成 + Web Search
        regulation_check_agent,# Agent3a: 規制チェック
        plan_revision_agent,   # Agent3b: 企画書修正
        brochure_gen_agent,    # Agent4: ブローシャ + 画像生成
        video_gen_agent,       # Agent5: 動画生成（Photo Avatar）
    ]
).build()

result = await workflow.run("沖縄3泊4日の夏季ファミリー向け企画を作って")
```

## Middleware

```python
from agent_framework import Middleware

class ContentSafetyMiddleware(Middleware):
    """入力時に Content Safety チェックを実行する middleware"""

    async def on_request(self, context, call_next):
        # Prompt Shield チェック
        is_safe = await check_prompt_shield(context.input)
        if not is_safe:
            raise ValueError("入力が Content Safety チェックに失敗しました")
        # 次の middleware / エージェントに渡す
        return await call_next()  # 引数なし
```

## 設定

```python
from agent_framework import load_settings
from typing import TypedDict

class AppSettings(TypedDict):
    project_endpoint: str
    model_name: str
    content_safety_endpoint: str

settings = load_settings(AppSettings)
```

## よくある間違い

| ✅ 正しい | ❌ 間違い |
|----------|---------|
| `FoundryChatClient` | `AzureOpenAIResponsesClient` |
| `client.as_agent(...)` | `Agent(chat_client=...)` |
| `@tool` | `@ai_function` |
| `await agent.run("文字列")` | `agent.run(Message(...))` |
| `SequentialBuilder(participants=[...]).build()` | `.participants()` fluent |
| `await call_next()` | `call_next(context)` |
| `TypedDict + load_settings()` | Pydantic Settings |
| `AZURE_AI_PROJECT_ENDPOINT` / `FOUNDRY_PROJECT_ENDPOINT` | `AZURE_OPENAI_ENDPOINT` |

## 参照

- Breaking Changes: https://learn.microsoft.com/en-us/agent-framework/support/upgrade/python-2026-significant-changes
- GitHub: https://github.com/microsoft/agent-framework
- PyPI: https://pypi.org/project/agent-framework/
