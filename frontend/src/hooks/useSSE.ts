/**
 * SSE 接続管理フック。パイプラインの状態を一元管理する。
 */

import { useCallback, useRef, useState } from 'react'
import { connectSSE, sendApproval, type SSEHandlers } from '../lib/sse-client'

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
  status: 'safe' | 'warning'
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
}

export function useSSE() {
  const [state, setState] = useState<PipelineState>(initialState)
  const conversationIdRef = useRef<string | null>(null)

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
        toolEvents: [...prev.toolEvents, data as ToolEvent],
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
      setState(prev => ({
        ...prev,
        metrics: doneData.metrics,
        status: 'completed',
        conversationId: doneData.conversation_id,
      }))
    },
  }), [])

  const sendMessage = useCallback(async (message: string) => {
    setState({ ...initialState, status: 'running' })
    const handlers = createHandlers()
    await connectSSE(message, handlers)
  }, [createHandlers])

  const approve = useCallback(async (response: string) => {
    const threadId = conversationIdRef.current
    if (!threadId) return
    setState(prev => ({ ...prev, status: 'running', approvalRequest: null }))
    const handlers = createHandlers()
    await sendApproval(threadId, response, handlers)
  }, [createHandlers])

  const reset = useCallback(() => {
    setState(initialState)
    conversationIdRef.current = null
  }, [])

  return { state, sendMessage, approve, reset }
}
