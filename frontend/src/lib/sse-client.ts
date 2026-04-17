/**
 * SSE クライアント。POST /api/chat に接続し、イベントをハンドラに振り分ける。
 */

import type { ConversationSettings, ModelSettings } from '../components/SettingsPanel'
import { getDelegatedApiHeaders } from './api-auth'

/** SSE タイムアウト（15 分 — 画像生成と動画待機を考慮） */
const SSE_TIMEOUT_MS = 900_000
const MANAGER_EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

export type SSEEventType =
  | 'agent_progress'
  | 'tool_event'
  | 'text'
  | 'image'
  | 'approval_request'
  | 'error'
  | 'done'

export type SSEHandlers = Partial<Record<SSEEventType, (data: unknown) => void>>

export interface RefineContext {
  source?: 'evaluation'
  artifactVersion?: number
}

export interface ChatRequestOptions {
  refineContext?: RefineContext
}

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
  conversationSettings?: ConversationSettings,
  options?: ChatRequestOptions,
): Promise<void> {
  const combinedSignal = buildSignal(signal)

  const body: Record<string, unknown> = { message, conversation_id: conversationId }
  if (options?.refineContext) {
    body.refine_context = {
      source: options.refineContext.source,
      artifact_version: options.refineContext.artifactVersion,
    }
  }
  if (settings) {
    const trimmedManagerEmail = settings.managerEmail.trim()
    if (settings.managerApprovalEnabled && !MANAGER_EMAIL_PATTERN.test(trimmedManagerEmail)) {
      handlers.error?.({ message: '有効な上司メールアドレスを入力してください', code: 'INVALID_MANAGER_EMAIL' })
      return
    }

    body.settings = {
      model: settings.model,
      temperature: settings.temperature,
      max_tokens: settings.maxTokens,
      top_p: settings.topP,
      iq_search_results: settings.iqSearchResults,
      iq_score_threshold: settings.iqScoreThreshold,
      image_settings: {
        image_model: settings.imageModel,
        image_quality: settings.imageQuality,
        image_width: settings.imageWidth,
        image_height: settings.imageHeight,
      },
    }
    body.workflow_settings = {
      manager_approval_enabled: settings.managerApprovalEnabled,
      manager_email: trimmedManagerEmail,
    }
  }
  if (conversationSettings && !conversationId) {
    body.conversation_settings = {
      work_iq_enabled: conversationSettings.workIqEnabled,
      source_scope: conversationSettings.workIqSourceScope,
      work_iq_source_scope: conversationSettings.workIqSourceScope,
    }
  }

  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (conversationSettings?.workIqEnabled) {
    Object.assign(
      headers,
      await getDelegatedApiHeaders({ interactive: !conversationId }),
    )
    headers['X-User-Timezone'] = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  }

  let response: Response
  try {
    response = await fetch('/api/chat', {
      method: 'POST',
      headers,
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
  useDelegatedAuth = false,
): Promise<void> {
  const combinedSignal = buildSignal(signal)
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (useDelegatedAuth) {
    Object.assign(headers, await getDelegatedApiHeaders())
  }

  let res: Response
  try {
    res = await fetch(`/api/chat/${threadId}/approve`, {
      method: 'POST',
      headers,
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
