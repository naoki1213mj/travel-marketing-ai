---
name: foundry-workflows
description: >-
  Foundry Agent Service Workflows の設計・実装パターン。
  Sequential Workflow、Human-in-the-Loop 承認フロー、Question ノード、
  Conversations API との接続、YAML 構成の書き方を提供する。
  Triggers: "Workflows", "ワークフロー", "Sequential", "Human-in-the-Loop",
  "承認フロー", "Question ノード", "Conversations API", "オーケストレーション"
---

# Foundry Agent Service Workflows パターン

## Workflow 構成（本プロジェクト）

```yaml
# Sequential + Human-in-the-Loop
workflow:
  type: sequential
  participants:
    - agent: data-search-agent        # Agent1: データ検索
    - agent: marketing-plan-agent     # Agent2: 施策生成
    - type: question                  # 承認ステップ
      prompt: |
        企画書の内容を確認してください。
        「承認」→ 規制チェックに進みます
        「修正」→ 修正指示を入力してください
      options:
        - label: 承認
          next: regulation-check-agent
        - label: 修正
          next: marketing-plan-agent   # ループバック
    - agent: regulation-check-agent   # Agent3: 規制チェック
    - agent: brochure-gen-agent       # Agent4: 販促物生成
```

## Conversations API 接続パターン

FastAPI バックエンドから Workflows を呼び出し、SSE でフロントエンドにストリーミングする。

```python
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

client = AIProjectClient(
    project_endpoint=os.environ["AZURE_AI_PROJECT_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

async def run_workflow(user_input: str):
    """Workflows を実行し、SSE イベントを生成する"""
    # Conversation を作成
    conversation = client.conversations.create()

    # メッセージを追加
    client.conversations.create_message(
        conversation_id=conversation.id,
        role="user",
        content=user_input,
    )

    # Workflow を実行（ストリーミング）
    stream = client.conversations.create_run_stream(
        conversation_id=conversation.id,
        agent_name="travel-marketing-workflow",
    )

    async for event in stream:
        # イベントをパースして SSE イベントに変換
        if event.type == "agent.start":
            yield WorkflowEvent(
                type=SSEEventType.AGENT_PROGRESS,
                data={"agent": event.agent_name}
            )
        elif event.type == "agent.end":
            yield WorkflowEvent(
                type=SSEEventType.AGENT_PROGRESS,
                data={"agent": event.agent_name}
            )
        elif event.type == "tool.call":
            yield WorkflowEvent(
                type=SSEEventType.TOOL_EVENT,
                data={"tool": event.tool_name, "args": event.arguments}
            )
        elif event.type == "text.delta":
            yield WorkflowEvent(
                type=SSEEventType.TEXT,
                data={"content": event.text}
            )
        elif event.type == "question":
            yield WorkflowEvent(
                type=SSEEventType.APPROVAL_REQUEST,
                data={
                    "prompt": event.prompt,
                    "options": event.options,
                    "conversation_id": conversation.id,
                }
            )
```

## 承認レスポンスの送信

```python
@app.post("/api/approve")
async def approve(request: Request):
    """承認/修正レスポンスを Workflows に送信する"""
    body = await request.json()
    client.conversations.create_message(
        conversation_id=body["conversation_id"],
        role="user",
        content=body["response"],  # "承認" or 修正指示テキスト
    )
    # Workflow が再開し、次のエージェントに進む
    return StreamingResponse(
        continue_workflow(body["conversation_id"]),
        media_type="text/event-stream",
    )
```

## 制約（2026-03 時点）

- Workflows は **Preview**。API が変更される可能性あり
- Question ノードのループバックは Workflow 定義で明示的に指定する
- Hosted Agent と組み合わせる場合、Hosted Agent 側も Preview
- Conversations API のストリーミングイベント形式は SDK バージョンで異なる場合がある

## 参照

- Workflows 概要: https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/workflow
- Workflows ブログ: https://devblogs.microsoft.com/foundry/introducing-multi-agent-workflows-in-foundry-agent-service/
