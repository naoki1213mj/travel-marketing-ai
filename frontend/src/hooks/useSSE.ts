/**
 * SSE 接続管理フック。パイプラインの状態を一元管理する。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_SETTINGS, type ModelSettings } from '../components/SettingsPanel'
import { connectSSE, sendApproval, type SSEHandlers } from '../lib/sse-client'

/** toolEvents の最大保持数 */
const MAX_TOOL_EVENTS = 50

export interface AgentProgress {
  agent: string
  status: 'running' | 'completed'
  step: number
  total_steps: number
}

export interface ToolEvent {
  tool: string
  status: string
  agent: string
}

export interface TextContent {
  content: string
  agent: string
  content_type?: string
}

export interface ImageContent {
  url: string
  alt: string
  agent: string
}

export interface ApprovalRequest {
  prompt: string
  conversation_id: string
  plan_markdown?: string
}

export interface SafetyResult {
  hate: number
  self_harm: number
  sexual: number
  violence: number
  status: 'safe' | 'warning' | 'error'
}

export interface PipelineMetrics {
  latency_seconds: number
  tool_calls: number
  total_tokens: number
}

export interface ErrorData {
  message: string
  code: string
}

export type PipelineStatus = 'idle' | 'running' | 'approval' | 'completed' | 'error'

export interface ArtifactSnapshot {
  textContents: TextContent[]
  images: ImageContent[]
}

export interface PipelineState {
  status: PipelineStatus
  conversationId: string | null
  agentProgress: AgentProgress | null
  toolEvents: ToolEvent[]
  textContents: TextContent[]
  images: ImageContent[]
  approvalRequest: ApprovalRequest | null
  safetyResult: SafetyResult | null
  metrics: PipelineMetrics | null
  error: ErrorData | null
  versions: ArtifactSnapshot[]
  currentVersion: number
  settings: ModelSettings
  userMessages: string[]
}

const initialState: PipelineState = {
  status: 'idle',
  conversationId: null,
  agentProgress: null,
  toolEvents: [],
  textContents: [],
  images: [],
  approvalRequest: null,
  safetyResult: null,
  metrics: null,
  error: null,
  versions: [],
  currentVersion: 0,
  settings: { ...DEFAULT_SETTINGS },
  userMessages: [],
}

export function useSSE() {
  const [state, setState] = useState<PipelineState>(initialState)
  const conversationIdRef = useRef<string | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const stateRef = useRef<PipelineState>(initialState)

  // stateRef を常に最新に保つ（effect 内で更新）
  useEffect(() => {
    stateRef.current = state
  })

  // アンマウント時に SSE 接続を中断する
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
    }
  }, [])

  const createHandlers = useCallback((): SSEHandlers => ({
    agent_progress: (data) => {
      const progress = data as AgentProgress
      setState(prev => ({
        ...prev,
        agentProgress: progress,
        status: progress.status === 'running' ? 'running' : prev.status,
      }))
    },
    tool_event: (data) => {
      setState(prev => ({
        ...prev,
        toolEvents: [...prev.toolEvents, data as ToolEvent].slice(-MAX_TOOL_EVENTS),
      }))
    },
    text: (data) => {
      setState(prev => ({
        ...prev,
        textContents: [...prev.textContents, data as TextContent],
      }))
    },
    image: (data) => {
      setState(prev => ({
        ...prev,
        images: [...prev.images, data as ImageContent],
      }))
    },
    approval_request: (data) => {
      const request = data as ApprovalRequest
      conversationIdRef.current = request.conversation_id
      setState(prev => ({
        ...prev,
        approvalRequest: request,
        status: 'approval',
        conversationId: request.conversation_id,
      }))
    },
    safety: (data) => {
      setState(prev => ({
        ...prev,
        safetyResult: data as SafetyResult,
      }))
    },
    error: (data) => {
      setState(prev => ({
        ...prev,
        error: data as ErrorData,
        status: 'error',
      }))
    },
    done: (data) => {
      const doneData = data as { conversation_id: string; metrics: PipelineMetrics }
      setState(prev => {
        const snapshot: ArtifactSnapshot = {
          textContents: prev.textContents,
          images: prev.images,
        }
        const newVersions = [...prev.versions, snapshot]
        return {
          ...prev,
          metrics: doneData.metrics,
          status: 'completed',
          conversationId: doneData.conversation_id,
          versions: newVersions,
          currentVersion: newVersions.length,
        }
      })
    },
  }), [])

  const sendMessage = useCallback(async (message: string) => {
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    const existingConversationId = conversationIdRef.current
    setState(prev => ({
      ...prev,
      status: 'running',
      error: null,
      approvalRequest: null,
      userMessages: [...prev.userMessages, message],
    }))
    const handlers = createHandlers()
    const currentSettings = stateRef.current.settings
    await connectSSE(message, handlers, existingConversationId || undefined, controller.signal, currentSettings)
  }, [createHandlers])

  const approve = useCallback(async (response: string) => {
    const threadId = conversationIdRef.current
    if (!threadId) return
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    setState(prev => ({ ...prev, status: 'running', approvalRequest: null }))
    const handlers = createHandlers()
    await sendApproval(threadId, response, handlers, controller.signal)
  }, [createHandlers])

  const reset = useCallback(() => {
    setState(initialState)
    conversationIdRef.current = null
  }, [])

  const restoreVersion = useCallback((version: number) => {
    setState(prev => {
      const snapshot = prev.versions[version - 1]
      if (!snapshot) return prev
      return {
        ...prev,
        textContents: snapshot.textContents,
        images: snapshot.images,
        currentVersion: version,
      }
    })
  }, [])

  const updateSettings = useCallback((settings: ModelSettings) => {
    setState(prev => ({ ...prev, settings }))
  }, [])

  return { state, sendMessage, approve, reset, restoreVersion, updateSettings }
}
