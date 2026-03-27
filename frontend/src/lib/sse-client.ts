/**
 * SSE クライアント。POST /api/chat に接続し、イベントをハンドラに振り分ける。
 */

export type SSEEventType =
  | 'agent_progress'
  | 'tool_event'
  | 'text'
  | 'image'
  | 'approval_request'
  | 'safety'
  | 'error'
  | 'done'

export type SSEHandlers = Partial<Record<SSEEventType, (data: unknown) => void>>

/**
 * POST リクエストで SSE ストリームに接続する
 */
export async function connectSSE(
  message: string,
  handlers: SSEHandlers,
  conversationId?: string,
): Promise<void> {
  const response = await fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  })

  if (!response.ok) {
    handlers.error?.({ message: `HTTP ${response.status}`, code: 'HTTP_ERROR' })
    return
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''

      for (const block of blocks) {
        if (!block.trim()) continue
        const eventMatch = block.match(/^event: (.+)$/m)
        const dataMatch = block.match(/^data: (.+)$/m)
        if (eventMatch && dataMatch) {
          const type = eventMatch[1] as SSEEventType
          const data: unknown = JSON.parse(dataMatch[1])
          handlers[type]?.(data)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * 承認レスポンスを送信する
 */
export async function sendApproval(
  threadId: string,
  response: string,
  handlers: SSEHandlers,
): Promise<void> {
  const res = await fetch(`/api/chat/${threadId}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: threadId, response }),
  })

  if (!res.ok) {
    handlers.error?.({ message: `HTTP ${res.status}`, code: 'HTTP_ERROR' })
    return
  }

  const reader = res.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''
      for (const block of blocks) {
        if (!block.trim()) continue
        const eventMatch = block.match(/^event: (.+)$/m)
        const dataMatch = block.match(/^data: (.+)$/m)
        if (eventMatch && dataMatch) {
          const type = eventMatch[1] as SSEEventType
          const data: unknown = JSON.parse(dataMatch[1])
          handlers[type]?.(data)
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}
