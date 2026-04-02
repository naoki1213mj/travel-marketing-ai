/**
 * SSE 接続管理フック。パイプラインの状態を一元管理する。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { DEFAULT_SETTINGS, type ModelSettings } from '../components/SettingsPanel'
import { cloneEvaluationRecord, type EvaluationRecord } from '../lib/evaluation'
import { connectSSE, sendApproval, type SSEHandlers } from '../lib/sse-client'

/** toolEvents の最大保持数 */
const MAX_TOOL_EVENTS = 50
const PIPELINE_TOTAL_STEPS = 5

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
  toolEvents: ToolEvent[]
  metrics: PipelineMetrics | null
  evaluations: EvaluationRecord[]
}

export interface PendingVersion {
  version: number
  textOffset: number
  imageOffset: number
  toolEventOffset: number
}

interface SnapshotSource {
  textContents: TextContent[]
  images: ImageContent[]
  toolEvents: ToolEvent[]
  metrics: PipelineMetrics | null
  evaluations?: EvaluationRecord[]
}

export interface ConversationEvent {
  event?: string
  data?: Record<string, unknown>
}

export interface ConversationDocument {
  id?: string
  input?: string
  status?: string
  messages?: ConversationEvent[]
}

export interface PipelineState {
  status: PipelineStatus
  conversationId: string | null
  agentProgress: AgentProgress | null
  toolEvents: ToolEvent[]
  textContents: TextContent[]
  images: ImageContent[]
  approvalRequest: ApprovalRequest | null
  metrics: PipelineMetrics | null
  error: ErrorData | null
  versions: ArtifactSnapshot[]
  currentVersion: number
  pendingVersion: PendingVersion | null
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
  metrics: null,
  error: null,
  versions: [],
  currentVersion: 0,
  pendingVersion: null,
  settings: { ...DEFAULT_SETTINGS },
  userMessages: [],
}

function cloneTextContents(textContents: TextContent[]): TextContent[] {
  return textContents.map(item => ({ ...item }))
}

function cloneImages(images: ImageContent[]): ImageContent[] {
  return images.map(item => ({ ...item }))
}

function cloneToolEvents(toolEvents: ToolEvent[]): ToolEvent[] {
  return toolEvents.map(item => ({ ...item }))
}

function cloneEvaluations(evaluations: EvaluationRecord[]): EvaluationRecord[] {
  return evaluations.map(cloneEvaluationRecord)
}

function buildEvaluationRecord(data: Record<string, unknown>, fallbackVersion: number): EvaluationRecord | null {
  const version = Number(data.version || fallbackVersion)
  const round = Number(data.round || 1)
  const createdAt = typeof data.created_at === 'string' ? data.created_at : new Date(0).toISOString()
  const result = data.result

  if (version < 1 || round < 1 || !result || typeof result !== 'object') {
    return null
  }

  return {
    version,
    round,
    createdAt,
    result: result as EvaluationRecord['result'],
  }
}

export function createArtifactSnapshot(source: SnapshotSource): ArtifactSnapshot {
  return {
    textContents: cloneTextContents(source.textContents),
    images: cloneImages(source.images),
    toolEvents: cloneToolEvents(source.toolEvents),
    metrics: source.metrics ? { ...source.metrics } : null,
    evaluations: cloneEvaluations(source.evaluations ?? []),
  }
}

function getLatestPlanMarkdown(textContents: TextContent[]): string | undefined {
  for (let index = textContents.length - 1; index >= 0; index -= 1) {
    const content = textContents[index]
    if (content.agent === 'plan-revision-agent' || content.agent === 'marketing-plan-agent') {
      return content.content
    }
  }
  return undefined
}

export function buildRestoredPipelineState(
  doc: ConversationDocument,
  conversationId: string,
  settings: ModelSettings,
): PipelineState {
  const textContents: TextContent[] = []
  const images: ImageContent[] = []
  let toolEvents: ToolEvent[] = []
  let metrics: PipelineMetrics | null = null
  let error: ErrorData | null = null
  let approvalRequest: ApprovalRequest | null = null
  let latestAgentProgress: AgentProgress | null = null
  const versions: ArtifactSnapshot[] = []

  for (const event of doc.messages ?? []) {
    const data = event.data ?? {}

    switch (event.event) {
      case 'agent_progress':
        latestAgentProgress = {
          agent: String(data.agent || ''),
          status: data.status === 'completed' ? 'completed' : 'running',
          step: Number(data.step || 0),
          total_steps: Number(data.total_steps || 0),
        }
        break
      case 'text':
        textContents.push({
          content: String(data.content || ''),
          agent: String(data.agent || ''),
          content_type: data.content_type ? String(data.content_type) : undefined,
        })
        break
      case 'image':
        images.push({
          url: String(data.url || ''),
          alt: String(data.alt || ''),
          agent: String(data.agent || ''),
        })
        break
      case 'tool_event':
        toolEvents = [
          ...toolEvents,
          {
            tool: String(data.tool || ''),
            status: String(data.status || ''),
            agent: String(data.agent || ''),
          },
        ].slice(-MAX_TOOL_EVENTS)
        break
      case 'approval_request':
        approvalRequest = {
          prompt: String(data.prompt || ''),
          conversation_id: String(data.conversation_id || conversationId),
          plan_markdown: data.plan_markdown ? String(data.plan_markdown) : undefined,
        }
        latestAgentProgress = {
          agent: 'approval',
          status: 'running',
          step: 3,
          total_steps: PIPELINE_TOTAL_STEPS,
        }
        break
      case 'error':
        error = {
          message: String(data.message || ''),
          code: String(data.code || ''),
        }
        break
      case 'done':
        metrics = (data.metrics as PipelineMetrics | undefined) ?? null
        versions.push(createArtifactSnapshot({ textContents, images, toolEvents, metrics, evaluations: [] }))
        break
      case 'evaluation_result': {
        const evaluation = buildEvaluationRecord(data, versions.length)
        if (!evaluation) break
        const snapshot = versions[evaluation.version - 1]
        if (!snapshot) break
        snapshot.evaluations = [...snapshot.evaluations, cloneEvaluationRecord(evaluation)]
        break
      }
    }
  }

  if (approvalRequest && !approvalRequest.plan_markdown) {
    approvalRequest = {
      ...approvalRequest,
      plan_markdown: getLatestPlanMarkdown(textContents),
    }
  }

  const status = doc.status === 'awaiting_approval'
    ? 'approval'
    : doc.status === 'error'
      ? 'error'
      : 'completed'

  if (status === 'completed' && versions.length === 0 && (textContents.length > 0 || images.length > 0)) {
    versions.push(createArtifactSnapshot({ textContents, images, toolEvents, metrics, evaluations: [] }))
  }

  return {
    ...initialState,
    status,
    conversationId,
    agentProgress: status === 'approval'
      ? latestAgentProgress ?? {
          agent: 'approval',
          status: 'running',
          step: 3,
          total_steps: PIPELINE_TOTAL_STEPS,
        }
      : latestAgentProgress,
    toolEvents: cloneToolEvents(toolEvents),
    textContents: cloneTextContents(textContents),
    images: cloneImages(images),
    approvalRequest: status === 'approval'
      ? approvalRequest ?? {
          prompt: '',
          conversation_id: conversationId,
          plan_markdown: getLatestPlanMarkdown(textContents),
        }
      : null,
    metrics,
    error,
    versions,
    currentVersion: versions.length,
    pendingVersion: null,
    settings: { ...settings },
    userMessages: doc.input ? [doc.input] : [],
  }
}

function syncToLatestSnapshot(state: PipelineState): PipelineState {
  const latestSnapshot = state.versions[state.versions.length - 1]
  if (!latestSnapshot || state.currentVersion === 0 || state.currentVersion === state.versions.length) {
    return state
  }

  return {
    ...state,
    textContents: cloneTextContents(latestSnapshot.textContents),
    images: cloneImages(latestSnapshot.images),
    toolEvents: cloneToolEvents(latestSnapshot.toolEvents),
    metrics: latestSnapshot.metrics ? { ...latestSnapshot.metrics } : null,
    currentVersion: state.versions.length,
  }
}

function hasLiveArtifacts(state: PipelineState): boolean {
  return state.textContents.length > 0 || state.images.length > 0 || state.toolEvents.length > 0
}

function hasUncommittedArtifacts(state: PipelineState, snapshot: ArtifactSnapshot): boolean {
  return state.textContents.length > snapshot.textContents.length
    || state.images.length > snapshot.images.length
    || state.toolEvents.length > snapshot.toolEvents.length
}

function ensureDraftSnapshot(state: PipelineState): PipelineState {
  if (state.versions.length > 0 || !hasLiveArtifacts(state)) {
    return state
  }

  const snapshot = createArtifactSnapshot({
    textContents: state.textContents,
    images: state.images,
    toolEvents: state.toolEvents,
    metrics: state.metrics,
    evaluations: [],
  })

  return {
    ...state,
    versions: [snapshot],
    currentVersion: 1,
  }
}

function inferPendingVersion(state: PipelineState): PendingVersion | null {
  if (state.pendingVersion) {
    return state.pendingVersion
  }

  if (!hasLiveArtifacts(state)) {
    return null
  }

  const latestSnapshot = state.versions[state.versions.length - 1]
  if (!latestSnapshot) {
    return {
      version: 1,
      textOffset: 0,
      imageOffset: 0,
      toolEventOffset: 0,
    }
  }

  if (!hasUncommittedArtifacts(state, latestSnapshot)) {
    return null
  }

  return {
    version: state.versions.length + 1,
    textOffset: latestSnapshot.textContents.length,
    imageOffset: latestSnapshot.images.length,
    toolEventOffset: latestSnapshot.toolEvents.length,
  }
}

export function useSSE() {
  const [state, setState] = useState<PipelineState>(initialState)
  const conversationIdRef = useRef<string | null>(null)
  const abortControllerRef = useRef<AbortController | null>(null)
  const stateRef = useRef<PipelineState>(initialState)
  const activeRequestIdRef = useRef(0)

  // stateRef を常に最新に保つ（effect 内で更新）
  useEffect(() => {
    stateRef.current = state
  })

  // アンマウント時に SSE 接続を中断する
  useEffect(() => {
    return () => {
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
    }
  }, [])

  const createHandlers = useCallback((requestId: number): SSEHandlers => ({
    agent_progress: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const progress = data as AgentProgress
      setState(prev => ({
        ...prev,
        agentProgress: progress,
        status: progress.status === 'running' ? 'running' : prev.status,
      }))
    },
    tool_event: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      setState(prev => ({
        ...prev,
        toolEvents: [...prev.toolEvents, data as ToolEvent].slice(-MAX_TOOL_EVENTS),
      }))
    },
    text: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      setState(prev => ({
        ...prev,
        textContents: [...prev.textContents, data as TextContent],
      }))
    },
    image: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const image = data as ImageContent
      if (!image.url?.trim()) return
      setState(prev => ({
        ...prev,
        images: [...prev.images, image],
      }))
    },
    approval_request: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const request = data as ApprovalRequest
      conversationIdRef.current = request.conversation_id
      setState(prev => ({
        ...prev,
        approvalRequest: {
          ...request,
          plan_markdown: request.plan_markdown || getLatestPlanMarkdown(prev.textContents),
        },
        status: 'approval',
        conversationId: request.conversation_id,
        agentProgress: {
          agent: 'approval',
          status: 'running',
          step: 3,
          total_steps: PIPELINE_TOTAL_STEPS,
        },
      }))
    },
    error: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      setState(prev => ({
        ...prev,
        error: data as ErrorData,
        status: 'error',
        pendingVersion: null,
      }))
    },
    done: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const doneData = data as { conversation_id: string; metrics: PipelineMetrics }
      setState(prev => {
        const snapshot = createArtifactSnapshot({
          textContents: prev.textContents,
          images: prev.images,
          toolEvents: prev.toolEvents,
          metrics: doneData.metrics,
          evaluations: [],
        })
        const newVersions = [...prev.versions, snapshot]
        return {
          ...prev,
          metrics: doneData.metrics,
          status: 'completed',
          conversationId: doneData.conversation_id,
          versions: newVersions,
          currentVersion: newVersions.length,
          pendingVersion: null,
        }
      })
    },
  }), [])

  const sendMessage = useCallback(async (message: string) => {
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    const requestId = activeRequestIdRef.current + 1
    activeRequestIdRef.current = requestId
    const existingConversationId = conversationIdRef.current
    setState(prev => ({
      ...(() => {
        const synced = ensureDraftSnapshot(syncToLatestSnapshot(prev))
        return {
          ...synced,
          status: 'running' as const,
          error: null,
          approvalRequest: null,
          agentProgress: null,
          pendingVersion: synced.versions.length > 0
            ? {
                version: synced.versions.length + 1,
                textOffset: synced.textContents.length,
                imageOffset: synced.images.length,
                toolEventOffset: synced.toolEvents.length,
              }
            : null,
          userMessages: [...synced.userMessages, message],
        }
      })(),
    }))
    const handlers = createHandlers(requestId)
    const currentSettings = stateRef.current.settings
    try {
      await connectSSE(message, handlers, existingConversationId || undefined, controller.signal, currentSettings)
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
    }
  }, [createHandlers])

  const approve = useCallback(async (response: string) => {
    const threadId = conversationIdRef.current
    if (!threadId) return
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    const requestId = activeRequestIdRef.current + 1
    activeRequestIdRef.current = requestId
    setState(prev => ({
      ...prev,
      status: 'running',
      approvalRequest: null,
      error: null,
      pendingVersion: inferPendingVersion(prev),
    }))
    const handlers = createHandlers(requestId)
    try {
      await sendApproval(threadId, response, handlers, controller.signal)
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
    }
  }, [createHandlers])

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    activeRequestIdRef.current += 1
    setState(initialState)
    conversationIdRef.current = null
  }, [])

  const restoreVersion = useCallback((version: number) => {
    setState(prev => {
      if (prev.pendingVersion) return prev
      const snapshot = prev.versions[version - 1]
      if (!snapshot) return prev
      return {
        ...prev,
        textContents: snapshot.textContents,
        images: snapshot.images,
        toolEvents: snapshot.toolEvents,
        metrics: snapshot.metrics,
        approvalRequest: null,
        error: null,
        currentVersion: version,
        pendingVersion: null,
      }
    })
  }, [])

  const updateSettings = useCallback((settings: ModelSettings) => {
    setState(prev => ({ ...prev, settings }))
  }, [])

  const saveEvaluation = useCallback((record: EvaluationRecord) => {
    setState(prev => {
      const seeded = ensureDraftSnapshot(prev)
      const targetIndex = record.version - 1
      const targetSnapshot = seeded.versions[targetIndex]
      if (!targetSnapshot) return prev

      return {
        ...seeded,
        currentVersion: Math.max(seeded.currentVersion, record.version),
        versions: seeded.versions.map((snapshot, index) => {
          if (index !== targetIndex) return snapshot
          return {
            ...snapshot,
            evaluations: [...snapshot.evaluations, cloneEvaluationRecord(record)],
          }
        }),
      }
    })
  }, [])

  /** 保存済み会話を復元する（新規推論を実行しない） */
  const restoreConversation = useCallback(async (conversationId: string) => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    const requestId = activeRequestIdRef.current + 1
    activeRequestIdRef.current = requestId

    try {
      const resp = await fetch(`/api/conversations/${conversationId}`)
      if (!resp.ok) return
      const doc = await resp.json() as ConversationDocument
      if (requestId !== activeRequestIdRef.current) return

      setState(buildRestoredPipelineState(doc, conversationId, stateRef.current.settings))

      conversationIdRef.current = conversationId
    } catch (err) {
      console.warn('会話の復元に失敗:', err)
    }
  }, [])

  return { state, sendMessage, approve, reset, restoreVersion, updateSettings, restoreConversation, saveEvaluation }
}
