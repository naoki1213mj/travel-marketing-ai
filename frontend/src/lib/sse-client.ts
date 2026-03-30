/**
 * SSE クライアント。POST /api/chat に接続し、イベントをハンドラに振り分ける。
 */

import type { ModelSettings } from '../components/SettingsPanel'

/** SSE タイムアウト（10 分 — Agent3+Agent4 の画像生成を考慮） */
const SSE_TIMEOUT_MS = 600_000

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
 * SSE ストリームを読み取る共通処理
 */
async function readSSEStream(
  response: Response,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split('\n\n')
      buffer = blocks.pop() || ''

      for (const block of blocks) {
        if (signal?.aborted) break
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
 * タイムアウト付きの AbortSignal を生成する。ユーザー提供の signal があれば合成する。
 */
function buildSignal(userSignal?: AbortSignal): AbortSignal {
  const timeoutSignal = AbortSignal.timeout(SSE_TIMEOUT_MS)
  if (userSignal) {
    return AbortSignal.any([userSignal, timeoutSignal])
  }
  return timeoutSignal
}

/**
 * POST リクエストで SSE ストリームに接続する
 */
export async function connectSSE(
  message: string,
  handlers: SSEHandlers,
  conversationId?: string,
  signal?: AbortSignal,
  settings?: ModelSettings,
): Promise<void> {
  const combinedSignal = buildSignal(signal)

  const body: Record<string, unknown> = { message, conversation_id: conversationId }
  if (settings) {
    body.settings = {
      model: settings.model,
      temperature: settings.temperature,
      max_tokens: settings.maxTokens,
      top_p: settings.topP,
      iq_search_results: settings.iqSearchResults,
      iq_score_threshold: settings.iqScoreThreshold,
    }
  }

  let response: Response
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: combinedSignal,
    })
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      handlers.error?.({ message: 'リクエストがタイムアウトまたはキャンセルされました', code: 'ABORT' })
      return
    }
    throw err
  }

  if (!response.ok) {
    handlers.error?.({ message: `HTTP ${response.status}`, code: 'HTTP_ERROR' })
    return
  }

  await readSSEStream(response, handlers, combinedSignal)
}

/**
 * 承認レスポンスを送信する
 */
export async function sendApproval(
  threadId: string,
  response: string,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const combinedSignal = buildSignal(signal)

  let res: Response
  try {
    res = await fetch(`/api/chat/${threadId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conversation_id: threadId, response }),
      signal: combinedSignal,
    })
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') {
      handlers.error?.({ message: 'リクエストがタイムアウトまたはキャンセルされました', code: 'ABORT' })
      return
    }
    throw err
  }

  if (!res.ok) {
    handlers.error?.({ message: `HTTP ${res.status}`, code: 'HTTP_ERROR' })
    return
  }

  await readSSEStream(res, handlers, combinedSignal)
}
