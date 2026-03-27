---
name: sse-content-safety
description: >-
  FastAPI の SSE ストリーミング設計と Content Safety 4 層防御の実装パターン。
  8 種類の SSE イベント定義、StreamingResponse の書き方、Prompt Shield の
  入力・ツール応答チェック、Content Filter、Text Analysis の統合方法を提供する。
  Triggers: "SSE", "ストリーミング", "StreamingResponse", "Content Safety",
  "Prompt Shield", "Content Filter", "Text Analysis", "event-stream"
---

# SSE ストリーミング + Content Safety 4 層防御

## SSE イベント定義（8 種類、§3.4 準拠）

```python
from enum import StrEnum

class SSEEventType(StrEnum):
    AGENT_PROGRESS = "agent_progress"     # どのエージェントが処理中か
    TOOL_EVENT = "tool_event"             # ツール呼び出しの開始・完了
    TEXT = "text"                         # テキストチャンク
    IMAGE = "image"                       # 画像生成結果
    APPROVAL_REQUEST = "approval_request" # Human-in-the-Loop 承認要求
    SAFETY = "safety"                     # Content Safety 分析結果
    ERROR = "error"                       # エラー
    DONE = "done"                         # パイプライン完了
```

## FastAPI SSE エンドポイント

```python
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json

app = FastAPI()

async def event_generator(user_input: str):
    """Workflows の結果を SSE イベントとして生成する"""
    # 1. 入力チェック（Prompt Shield）
    shield_result = await check_prompt_shield(user_input)
    if not shield_result.is_safe:
        yield format_sse(SSEEventType.ERROR, {
            "message": "入力が安全性チェックに失敗しました",
            "code": "PROMPT_SHIELD_BLOCKED"
        })
        return

    # 2. Workflows 実行（各エージェントの進捗を SSE で送信）
    async for event in run_workflow(user_input):
        yield format_sse(event.type, event.data)

    # 3. 出力チェック（Text Analysis）
    safety_scores = await analyze_content(final_output)
    yield format_sse(SSEEventType.SAFETY, {
        "hate": safety_scores.hate,
        "self_harm": safety_scores.self_harm,
        "sexual": safety_scores.sexual,
        "violence": safety_scores.violence,
    })

def format_sse(event_type: str, data: dict) -> str:
    """SSE フォーマットに変換する"""
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    return StreamingResponse(
        event_generator(body["message"]),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
```

## Content Safety 4 層防御

| 層 | タイミング | 実装場所 | 検出対象 |
|---|---|---|---|
| 1. Prompt Shield（入力） | ユーザー入力受信直後 | FastAPI `/api/chat` | プロンプトインジェクション、Jailbreak |
| 2. Content Filter（モデル） | gpt-5.4-mini 呼び出し時 | Foundry デプロイメント設定 | 有害コンテンツ（入出力両方） |
| 3. Prompt Shield（ツール応答） | Web Search / MCP の応答 | Foundry ガードレール設定 | 間接プロンプトインジェクション |
| 4. Text Analysis（出力） | パイプライン完了後 | FastAPI | Hate/SelfHarm/Sexual/Violence |

### Prompt Shield の呼び出し

```python
from azure.ai.contentsafety import ContentSafetyClient
from azure.identity import DefaultAzureCredential

client = ContentSafetyClient(
    endpoint=os.environ["CONTENT_SAFETY_ENDPOINT"],
    credential=DefaultAzureCredential(),
)

async def check_prompt_shield(user_input: str) -> ShieldResult:
    """Prompt Shield でユーザー入力をチェックする"""
    response = client.analyze_text(
        text=user_input,
        categories=["Hate", "SelfHarm", "Sexual", "Violence"],
        output_type="FourSeverityLevels",
    )
    # Jailbreak 検出
    shield_response = client.detect_jailbreak(text=user_input)
    is_safe = (
        all(c.severity == 0 for c in response.categories_analysis)
        and not shield_response.jailbreak_detected
    )
    return ShieldResult(is_safe=is_safe, details=response)
```

## フロントエンド SSE クライアント

```typescript
// frontend/src/lib/sse-client.ts
export async function connectSSE(
  message: string,
  handlers: Record<string, (data: any) => void>
) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n\n");
    buffer = lines.pop() || "";

    for (const block of lines) {
      const eventMatch = block.match(/^event: (.+)$/m);
      const dataMatch = block.match(/^data: (.+)$/m);
      if (eventMatch && dataMatch) {
        const type = eventMatch[1];
        const data = JSON.parse(dataMatch[1]);
        handlers[type]?.(data);
      }
    }
  }
}
```
