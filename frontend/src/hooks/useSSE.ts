/**
 * SSE 接続管理フック。パイプラインの状態を一元管理する。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import {
  DEFAULT_CONVERSATION_SETTINGS,
  DEFAULT_SETTINGS,
  normalizeModelSettings,
  type ConversationSettings,
  type ModelSettings,
  type WorkIqSourceScope,
  type WorkIqUiStatus,
} from '../components/SettingsPanel'
import { isApprovalResponseText } from '../lib/approval-flow'
import { getDelegatedApiAuth } from '../lib/api-auth'
import {
  normalizeChartSpecs,
  normalizeDebugEvents,
  normalizeEvidenceItems,
  normalizePipelineMetrics,
  normalizeSourceIngestionStates,
  normalizeTraceEvents,
  normalizeWorkIqSourceMetadata,
  type ChartSpec,
  type DebugEvent,
  type EvidenceItem,
  type PipelineMetrics,
  type SourceIngestionState,
  type TraceEvent,
  type WorkIqSourceMetadata,
} from '../lib/event-schemas'
import { consumeMsalRedirectFailureSentinel } from '../lib/msal-redirect-sentinel'
import { cloneEvaluationRecord, type EvaluationRecord } from '../lib/evaluation'
import { connectSSE, sendApproval, type ChatRequestOptions, type SSEHandlers } from '../lib/sse-client'
import { normalizeToolEventData, type ToolEvent } from '../lib/tool-events'

export type { ToolEvent } from '../lib/tool-events'
export type { PipelineMetrics } from '../lib/event-schemas'

/** toolEvents の最大保持数 */
const MAX_TOOL_EVENTS = 50
const PIPELINE_TOTAL_STEPS = 5
const DRAFT_EVALUATION_CACHE_KEY = '__draft__'
const PENDING_WORKIQ_REQUEST_KEY = 'workIqPendingChatRequest'

export interface AgentProgress {
  agent: string
  status: 'running' | 'completed'
  step: number
  total_steps: number
}

export interface TextContent {
  content: string
  agent: string
  content_type?: string
  evidence?: EvidenceItem[]
  charts?: ChartSpec[]
  trace_events?: TraceEvent[]
  debug_events?: DebugEvent[]
  source_metadata?: WorkIqSourceMetadata[]
  source_ingestion?: SourceIngestionState[]
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
  approval_scope?: 'user' | 'manager'
  manager_email?: string
  manager_comment?: string
  manager_approval_url?: string
  manager_delivery_mode?: 'manual' | 'workflow'
}

export interface ErrorData {
  message: string
  code: string
  consent_link?: string
  consentLink?: string
  auth_link?: string
  authLink?: string
}

export type PipelineStatus = 'idle' | 'running' | 'approval' | 'completed' | 'error'

export interface ArtifactSnapshot {
  textContents: TextContent[]
  images: ImageContent[]
  toolEvents: ToolEvent[]
  metrics: PipelineMetrics | null
  evaluations: EvaluationRecord[]
  isDraft?: boolean
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
  isDraft?: boolean
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
  metadata?: Record<string, unknown>
}

export interface WorkIqState extends ConversationSettings {
  status: WorkIqUiStatus
  rawStatus?: string
}

export interface PipelineState {
  status: PipelineStatus
  conversationId: string | null
  managerApprovalPolling: boolean
  backgroundUpdatesPending: boolean
  hasManagerApprovalPhase: boolean
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
  conversationSettings: ConversationSettings
  draftConversationSettings: ConversationSettings
  workIq: WorkIqState
  userMessages: string[]
}

export interface SendMessageOptions extends ChatRequestOptions {
  resumeState?: {
    settings: ModelSettings
    conversationSettings: ConversationSettings
  }
}

export interface RestoreConversationOptions {
  passive?: boolean
}

function getAuthRedirectUrl(payload: Record<string, unknown>): string {
  for (const key of ['consent_link', 'consentLink', 'auth_link', 'authLink']) {
    const value = payload[key]
    if (typeof value === 'string') {
      const trimmed = value.trim()
      if (trimmed) return trimmed
    }
  }
  return ''
}

function isAllowedAuthRedirectUrl(rawUrl: string): boolean {
  try {
    const url = new URL(rawUrl)
    const hostname = url.hostname.toLowerCase()
    return url.protocol === 'https:'
      && (
        hostname === 'login.microsoftonline.com'
        || hostname.endsWith('.login.microsoftonline.com')
        || hostname === 'login.microsoft.com'
        || hostname.endsWith('.login.microsoft.com')
      )
  } catch {
    return false
  }
}

function buildBlockedAuthRedirectError(): ErrorData {
  return {
    message: 'Work IQ の認証リンクが許可された Microsoft ログイン URL ではないためブロックしました。',
    code: 'WORKIQ_AUTH_REDIRECT_BLOCKED',
  }
}

function buildWorkIqDelegatedAuthError(status: string): ErrorData {
  switch (status) {
    case 'auth_required':
      return { message: 'Work IQ の利用にはサインインが必要です', code: 'WORKIQ_AUTH_REQUIRED' }
    case 'consent_required':
      return { message: 'Work IQ の利用には管理者の同意が必要です', code: 'WORKIQ_CONSENT_REQUIRED' }
    default:
      return { message: 'Work IQ の委任認証を確認できませんでした', code: 'WORKIQ_AUTH_UNAVAILABLE' }
  }
}

const initialState: PipelineState = {
  status: 'idle',
  conversationId: null,
  managerApprovalPolling: false,
  backgroundUpdatesPending: false,
  hasManagerApprovalPhase: false,
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
  conversationSettings: { ...DEFAULT_CONVERSATION_SETTINGS, workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope] },
  draftConversationSettings: { ...DEFAULT_CONVERSATION_SETTINGS, workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope] },
  workIq: {
    ...DEFAULT_CONVERSATION_SETTINGS,
    workIqSourceScope: [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope],
    status: 'off',
  },
  userMessages: [],
}

function cloneTextContents(textContents: TextContent[]): TextContent[] {
  return textContents.map(item => ({
    ...item,
    evidence: item.evidence ? normalizeEvidenceItems(item.evidence) : undefined,
    charts: item.charts ? normalizeChartSpecs(item.charts) : undefined,
    trace_events: item.trace_events ? normalizeTraceEvents(item.trace_events) : undefined,
    debug_events: item.debug_events ? normalizeDebugEvents(item.debug_events) : undefined,
    source_metadata: item.source_metadata ? normalizeWorkIqSourceMetadata(item.source_metadata) : undefined,
    source_ingestion: item.source_ingestion ? normalizeSourceIngestionStates(item.source_ingestion) : undefined,
  }))
}

function cloneImages(images: ImageContent[]): ImageContent[] {
  return images.map(item => ({ ...item }))
}

function cloneToolEvents(toolEvents: ToolEvent[]): ToolEvent[] {
  return toolEvents.map(item => ({
    ...item,
    source_scope: item.source_scope ? [...item.source_scope] : undefined,
    evidence: item.evidence ? normalizeEvidenceItems(item.evidence) : undefined,
    charts: item.charts ? normalizeChartSpecs(item.charts) : undefined,
    trace_events: item.trace_events ? normalizeTraceEvents(item.trace_events) : undefined,
    debug_events: item.debug_events ? normalizeDebugEvents(item.debug_events) : undefined,
  }))
}

function clonePipelineMetrics(metrics: PipelineMetrics | null): PipelineMetrics | null {
  return normalizePipelineMetrics(metrics)
}

function normalizeTextContentData(data: Record<string, unknown>): TextContent {
  return {
    content: String(data.content || ''),
    agent: String(data.agent || ''),
    content_type: data.content_type ? String(data.content_type) : undefined,
    evidence: normalizeEvidenceItems(data.evidence),
    charts: normalizeChartSpecs(data.charts),
    trace_events: normalizeTraceEvents(data.trace_events),
    debug_events: normalizeDebugEvents(data.debug_events),
    source_metadata: normalizeWorkIqSourceMetadata(data.source_metadata),
    source_ingestion: normalizeSourceIngestionStates(data.source_ingestion),
  }
}

function cloneConversationSettings(settings: ConversationSettings): ConversationSettings {
  return {
    workIqEnabled: settings.workIqEnabled,
    workIqSourceScope: [...settings.workIqSourceScope],
  }
}

interface PendingWorkIqRequest {
  message: string
  settings: ModelSettings
  conversationSettings: ConversationSettings
  options?: ChatRequestOptions
}

function loadPendingWorkIqRequest(): PendingWorkIqRequest | null {
  try {
    const raw = window.sessionStorage.getItem(PENDING_WORKIQ_REQUEST_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PendingWorkIqRequest>
    if (typeof parsed.message !== 'string' || !parsed.message.trim()) return null
    if (!parsed.settings || typeof parsed.settings !== 'object') return null
    if (!parsed.conversationSettings || typeof parsed.conversationSettings !== 'object') return null

    const conversationSettings = cloneConversationSettings({
      workIqEnabled: parsed.conversationSettings.workIqEnabled === true,
      workIqSourceScope: parseWorkIqSourceScope(parsed.conversationSettings.workIqSourceScope),
    })

    return {
      message: parsed.message,
      settings: normalizeModelSettings({ ...DEFAULT_SETTINGS, ...parsed.settings }),
      conversationSettings,
      options: parsed.options,
    }
  } catch {
    return null
  }
}

function savePendingWorkIqRequest(request: PendingWorkIqRequest): void {
  try {
    window.sessionStorage.setItem(
      PENDING_WORKIQ_REQUEST_KEY,
      JSON.stringify({
        message: request.message,
        settings: normalizeModelSettings(request.settings),
        conversationSettings: cloneConversationSettings(request.conversationSettings),
        options: request.options,
      }),
    )
  } catch {
    // no-op
  }
}

function clearPendingWorkIqRequest(): void {
  try {
    window.sessionStorage.removeItem(PENDING_WORKIQ_REQUEST_KEY)
  } catch {
    // no-op
  }
}

function createWorkIqState(
  settings: ConversationSettings,
  status: WorkIqUiStatus = settings.workIqEnabled ? 'ready' : 'off',
  rawStatus?: string,
): WorkIqState {
  return {
    ...cloneConversationSettings(settings),
    status,
    rawStatus,
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function toBoolean(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true') return true
    if (normalized === 'false') return false
  }
  return null
}

function normalizeWorkIqSource(value: unknown): WorkIqSourceScope | null {
  const normalized = String(value || '').trim().toLowerCase().replace(/[\s/-]+/g, '_')
  switch (normalized) {
    case 'meeting_notes':
    case 'meetings':
    case 'meeting':
      return 'meeting_notes'
    case 'emails':
    case 'email':
      return 'emails'
    case 'teams_chats':
    case 'teams_chat':
    case 'teams':
    case 'chats':
      return 'teams_chats'
    case 'documents_notes':
    case 'documents':
    case 'docs':
    case 'notes':
    case 'documents_and_notes':
      return 'documents_notes'
    default:
      return null
  }
}

function parseWorkIqSourceScope(raw: unknown): WorkIqSourceScope[] {
  const sourceList = Array.isArray(raw)
    ? raw
    : isRecord(raw) && Array.isArray(raw.source_scope)
      ? raw.source_scope
      : []
  const normalized = sourceList
    .map(normalizeWorkIqSource)
    .filter((value): value is WorkIqSourceScope => value !== null)

  if (normalized.length === 0) {
    return [...DEFAULT_CONVERSATION_SETTINGS.workIqSourceScope]
  }

  return [...new Set(normalized)]
}

function normalizeWorkIqStatus(
  rawStatus: unknown,
  enabled: boolean,
  fallback: WorkIqUiStatus = enabled ? 'ready' : 'off',
): WorkIqUiStatus {
  const normalized = String(rawStatus || '').trim().toLowerCase()
  switch (normalized) {
    case 'completed':
    case 'ok':
    case 'enabled':
      return 'enabled'
    case 'auth_required':
    case 'sign_in_required':
      return 'sign_in_required'
    case 'consent_required':
      return 'consent_required'
    case 'unavailable':
    case 'timeout':
    case 'identity_mismatch':
    case 'failed':
    case 'error':
      return 'unavailable'
    case 'ready':
      return enabled ? 'ready' : 'off'
    default:
      return fallback
  }
}

function getWorkIqStateFromMetadata(metadata: Record<string, unknown>): WorkIqState {
  const conversationSettings = isRecord(metadata.conversation_settings) ? metadata.conversation_settings : null
  const nestedWorkIq = conversationSettings && isRecord(conversationSettings.work_iq) ? conversationSettings.work_iq : null
  const workIqSession = isRecord(metadata.work_iq_session) ? metadata.work_iq_session : null
  const briefSourceMetadata = workIqSession && isRecord(workIqSession.brief_source_metadata)
    ? workIqSession.brief_source_metadata
    : null
  const enabled = (
    toBoolean(workIqSession?.enabled)
    ?? toBoolean(nestedWorkIq?.enabled)
    ?? toBoolean(conversationSettings?.work_iq_enabled)
    ?? toBoolean(metadata.work_iq_enabled)
    ?? false
  )
  const sourceScope = parseWorkIqSourceScope(
    workIqSession?.source_scope
    ?? nestedWorkIq?.source_scope
    ?? conversationSettings?.source_scope
    ?? conversationSettings?.work_iq_source_scope
    ?? metadata.work_iq_source_scope
    ?? briefSourceMetadata?.source_scope
    ?? briefSourceMetadata?.sources,
  )
  const rawStatus = String(
    workIqSession?.status
    ?? workIqSession?.status_code
    ?? workIqSession?.warning_code
    ?? nestedWorkIq?.status
    ?? conversationSettings?.work_iq_status
    ?? metadata.work_iq_status
    ?? '',
  ).trim()

  return createWorkIqState(
    {
      workIqEnabled: enabled,
      workIqSourceScope: sourceScope,
    },
    normalizeWorkIqStatus(rawStatus, enabled),
    rawStatus || undefined,
  )
}

function applyWorkIqToolEvent(current: WorkIqState, event: ToolEvent): WorkIqState {
  if (event.source !== 'workiq') {
    return current
  }

  const nextSettings: ConversationSettings = {
    workIqEnabled: true,
    workIqSourceScope: event.source_scope && event.source_scope.length > 0
      ? [...event.source_scope]
      : current.workIqSourceScope,
  }
  const rawStatus = String(event.status || '').trim()
  const fallbackStatus = current.workIqEnabled ? current.status : 'ready'
  const nextStatus = rawStatus.toLowerCase() === 'running'
    ? fallbackStatus
    : normalizeWorkIqStatus(rawStatus, true, fallbackStatus)

  return createWorkIqState(nextSettings, nextStatus, rawStatus || current.rawStatus)
}

function resolveToolEventVersion(state: PipelineState): number {
  if (state.pendingVersion) {
    return state.pendingVersion.version
  }
  if (state.currentVersion > 0) {
    return state.currentVersion
  }
  if (state.versions.length > 0) {
    return state.versions.length
  }
  return 1
}

function cloneEvaluations(evaluations: EvaluationRecord[]): EvaluationRecord[] {
  return evaluations.map(cloneEvaluationRecord)
}

function getEvaluationCacheKey(conversationId: string | null | undefined): string {
  return conversationId ? `conversation:${conversationId}` : DRAFT_EVALUATION_CACHE_KEY
}

function getEvaluationRecordKey(record: EvaluationRecord): string {
  return `${record.version}:${record.round}:${record.createdAt}`
}

function mergeEvaluationRecords(existing: EvaluationRecord[], incoming: EvaluationRecord[]): EvaluationRecord[] {
  const merged = [...existing]
  const seen = new Set(existing.map(getEvaluationRecordKey))

  for (const record of incoming) {
    const key = getEvaluationRecordKey(record)
    if (seen.has(key)) continue
    merged.push(cloneEvaluationRecord(record))
    seen.add(key)
  }

  return merged
}

function applyEvaluationRecord(state: PipelineState, record: EvaluationRecord): PipelineState {
  const seeded = ensureDraftSnapshot(state)
  const targetIndex = record.version - 1
  const targetSnapshot = seeded.versions[targetIndex]
  if (!targetSnapshot) return state

  const evaluationKey = getEvaluationRecordKey(record)
  if (targetSnapshot.evaluations.some(existing => getEvaluationRecordKey(existing) == evaluationKey)) {
    return seeded
  }

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
}

function mergeCachedEvaluationsIntoState(state: PipelineState, records: EvaluationRecord[]): PipelineState {
  return records.reduce((currentState, record) => applyEvaluationRecord(currentState, record), state)
}

function preserveViewedCommittedVersion(
  previousState: PipelineState,
  restoredState: PipelineState,
  passive: boolean,
): PipelineState {
  if (!passive || previousState.conversationId !== restoredState.conversationId) {
    return restoredState
  }

  if (previousState.pendingVersion) {
    return restoredState
  }

  if (previousState.currentVersion < 1 || previousState.currentVersion >= previousState.versions.length) {
    return restoredState
  }

  const preservedVersion = Math.min(previousState.currentVersion, restoredState.versions.length)
  const snapshot = restoredState.versions[preservedVersion - 1]
  if (!snapshot || preservedVersion === restoredState.versions.length) {
    return restoredState
  }

  return {
    ...restoredState,
    textContents: cloneTextContents(snapshot.textContents),
    images: cloneImages(snapshot.images),
    toolEvents: cloneToolEvents(snapshot.toolEvents),
    metrics: clonePipelineMetrics(snapshot.metrics),
    currentVersion: preservedVersion,
  }
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
    metrics: clonePipelineMetrics(source.metrics),
    evaluations: cloneEvaluations(source.evaluations ?? []),
    isDraft: source.isDraft === true,
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

function getRestoredUserMessages(doc: ConversationDocument): string[] {
  const metadata = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata : {}
  const metadataMessages = Array.isArray(metadata.user_messages)
    ? metadata.user_messages
    : Array.isArray(metadata.userMessages)
      ? metadata.userMessages
      : null

  if (metadataMessages) {
    const restored = metadataMessages
      .filter((message): message is string => typeof message === 'string')
      .map(message => message.trim())
      .filter(Boolean)

    if (restored.length > 0) {
      return restored
    }
  }

  const fallbackInput = doc.input?.trim()
  return fallbackInput ? [fallbackInput] : []
}

function isBackgroundUpdate(data: Record<string, unknown>): boolean {
  return data.background_update === true
}

function syncLatestCompletedSnapshot(
  prev: PipelineState,
  source: SnapshotSource,
): ArtifactSnapshot[] {
  if (prev.status !== 'completed' || prev.pendingVersion || prev.currentVersion === 0 || prev.currentVersion !== prev.versions.length) {
    return prev.versions
  }

  const latestIndex = prev.versions.length - 1
  return prev.versions.map((snapshot, index) => (
    index === latestIndex
      ? createArtifactSnapshot(source)
      : snapshot
  ))
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
  let hasManagerApprovalPhase = false
  const versions: ArtifactSnapshot[] = []
  const pendingEvaluations = new Map<number, EvaluationRecord[]>()
  const metadata = doc.metadata && typeof doc.metadata === 'object' ? doc.metadata : {}
  const backgroundUpdatesPending = metadata.background_updates_pending === true
  let workIq = getWorkIqStateFromMetadata(metadata)
  let activeVersion = 1

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
        textContents.push(normalizeTextContentData(data))
        if (isBackgroundUpdate(data) && versions.length > 0) {
          versions[versions.length - 1] = createArtifactSnapshot({
            textContents,
            images,
            toolEvents,
            metrics: versions[versions.length - 1].metrics,
            evaluations: versions[versions.length - 1].evaluations,
          })
        }
        break
      case 'image':
        images.push({
          url: String(data.url || ''),
          alt: String(data.alt || ''),
          agent: String(data.agent || ''),
        })
        if (isBackgroundUpdate(data) && versions.length > 0) {
          versions[versions.length - 1] = createArtifactSnapshot({
            textContents,
            images,
            toolEvents,
            metrics: versions[versions.length - 1].metrics,
            evaluations: versions[versions.length - 1].evaluations,
          })
        }
        break
      case 'tool_event': {
        const requestedVersion = Number(data.version || 0)
        const resolvedVersion = Number.isFinite(requestedVersion) && requestedVersion > 0
          ? requestedVersion
          : isBackgroundUpdate(data) && versions.length > 0
            ? versions.length
            : activeVersion
        toolEvents = [
          ...toolEvents,
          normalizeToolEventData(data, {
            fallbackVersion: resolvedVersion,
            parseSourceScope: (raw) => (raw === undefined ? undefined : parseWorkIqSourceScope(raw)),
          }),
        ].slice(-MAX_TOOL_EVENTS)
        workIq = applyWorkIqToolEvent(workIq, toolEvents[toolEvents.length - 1])
        if (isBackgroundUpdate(data) && versions.length > 0) {
          versions[versions.length - 1] = createArtifactSnapshot({
            textContents,
            images,
            toolEvents,
            metrics: versions[versions.length - 1].metrics,
            evaluations: versions[versions.length - 1].evaluations,
          })
        }
        break
      }
      case 'approval_request':
        hasManagerApprovalPhase = hasManagerApprovalPhase || data.approval_scope === 'manager'
        approvalRequest = {
          prompt: String(data.prompt || ''),
          conversation_id: String(data.conversation_id || conversationId),
          plan_markdown: data.plan_markdown ? String(data.plan_markdown) : undefined,
          approval_scope: data.approval_scope === 'manager' ? 'manager' : 'user',
          manager_email: data.manager_email ? String(data.manager_email) : undefined,
          manager_comment: data.manager_comment ? String(data.manager_comment) : undefined,
          manager_approval_url: data.manager_approval_url ? String(data.manager_approval_url) : undefined,
          manager_delivery_mode: data.manager_delivery_mode === 'workflow' ? 'workflow' : data.manager_delivery_mode === 'manual' ? 'manual' : undefined,
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
        metrics = normalizePipelineMetrics(data.metrics)
        {
          const versionNumber = versions.length + 1
          versions.push(createArtifactSnapshot({
            textContents,
            images,
            toolEvents,
            metrics,
            evaluations: pendingEvaluations.get(versionNumber) ?? [],
          }))
          pendingEvaluations.delete(versionNumber)
          activeVersion = versionNumber + 1
        }
        break
      case 'evaluation_result': {
        const evaluation = buildEvaluationRecord(data, Math.max(versions.length, 1))
        if (!evaluation) break
        const snapshot = versions[evaluation.version - 1]
        if (!snapshot) {
          pendingEvaluations.set(
            evaluation.version,
            [...(pendingEvaluations.get(evaluation.version) ?? []), cloneEvaluationRecord(evaluation)],
          )
          break
        }

        snapshot.evaluations = [...snapshot.evaluations, cloneEvaluationRecord(evaluation)]
        if (isBackgroundUpdate(data) && versions.length > 0) {
          versions[versions.length - 1] = createArtifactSnapshot({
            textContents,
            images,
            toolEvents,
            metrics: versions[versions.length - 1].metrics,
            evaluations: versions[versions.length - 1].evaluations,
          })
        }
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

  const status = doc.status === 'awaiting_approval' || doc.status === 'awaiting_manager_approval'
    ? 'approval'
    : doc.status === 'running'
      ? 'running'
      : doc.status === 'error'
        ? 'error'
        : 'completed'

  const restoredRunningAgentProgress = status === 'running' && approvalRequest?.approval_scope === 'manager'
    ? latestAgentProgress && latestAgentProgress.agent !== 'approval'
      ? latestAgentProgress
      : {
          agent: 'brochure-gen-agent',
          status: 'running' as const,
          step: PIPELINE_TOTAL_STEPS,
          total_steps: PIPELINE_TOTAL_STEPS,
        }
    : latestAgentProgress
  const managerApprovalPolling = approvalRequest?.approval_scope === 'manager'
    && (doc.status === 'awaiting_manager_approval' || doc.status === 'running')
  const pendingVersion = ((status === 'approval' || status === 'running') && versions.length > 0)
    ? (() => {
        const latestSnapshot = versions[versions.length - 1]
        const hasUncommittedArtifacts = textContents.length > latestSnapshot.textContents.length
          || images.length > latestSnapshot.images.length
          || toolEvents.length > latestSnapshot.toolEvents.length

        if (!hasUncommittedArtifacts) {
          return null
        }

        return {
          version: latestSnapshot.isDraft ? versions.length : versions.length + 1,
          textOffset: latestSnapshot.textContents.length,
          imageOffset: latestSnapshot.images.length,
          toolEventOffset: latestSnapshot.toolEvents.length,
        }
      })()
    : null

  if (status === 'completed' && versions.length === 0 && (textContents.length > 0 || images.length > 0)) {
    versions.push(createArtifactSnapshot({
      textContents,
      images,
      toolEvents,
      metrics,
      evaluations: pendingEvaluations.get(1) ?? [],
    }))
  }

  return {
    ...initialState,
    status,
    conversationId,
    managerApprovalPolling,
    backgroundUpdatesPending,
    hasManagerApprovalPhase: hasManagerApprovalPhase || doc.status === 'awaiting_manager_approval',
    agentProgress: status === 'approval'
      ? latestAgentProgress ?? {
          agent: 'approval',
          status: 'running',
          step: 3,
          total_steps: PIPELINE_TOTAL_STEPS,
        }
      : restoredRunningAgentProgress,
    toolEvents: cloneToolEvents(toolEvents),
    textContents: cloneTextContents(textContents),
    images: cloneImages(images),
    approvalRequest: status === 'approval'
      ? approvalRequest ?? {
          prompt: '',
          conversation_id: conversationId,
          plan_markdown: getLatestPlanMarkdown(textContents),
          approval_scope: doc.status === 'awaiting_manager_approval' ? 'manager' : 'user',
        }
      : null,
    metrics,
    error,
    versions,
    currentVersion: versions.length,
    pendingVersion,
    settings: { ...settings },
    conversationSettings: cloneConversationSettings({
      workIqEnabled: workIq.workIqEnabled,
      workIqSourceScope: workIq.workIqSourceScope,
    }),
    workIq,
    userMessages: getRestoredUserMessages(doc),
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
    isDraft: true,
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

  if (latestSnapshot.isDraft) {
    return {
      version: state.versions.length,
      textOffset: latestSnapshot.textContents.length,
      imageOffset: latestSnapshot.images.length,
      toolEventOffset: latestSnapshot.toolEvents.length,
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
  const conversationEtagsRef = useRef<Record<string, string>>({})
  const abortControllerRef = useRef<AbortController | null>(null)
  const stateRef = useRef<PipelineState>(initialState)
  const activeRequestIdRef = useRef(0)
  const activeRestoreRequestIdRef = useRef(0)
  const localEvaluationCacheRef = useRef<Record<string, EvaluationRecord[]>>({})
  const attemptedPendingResumeRef = useRef(false)

  const cacheEvaluationRecord = useCallback((conversationId: string | null | undefined, record: EvaluationRecord) => {
    const cacheKey = getEvaluationCacheKey(conversationId)
    const existing = localEvaluationCacheRef.current[cacheKey] ?? []
    localEvaluationCacheRef.current[cacheKey] = mergeEvaluationRecords(existing, [record])
  }, [])

  const migrateCachedEvaluations = useCallback((fromConversationId: string | null | undefined, toConversationId: string | null | undefined) => {
    const fromKey = getEvaluationCacheKey(fromConversationId)
    const toKey = getEvaluationCacheKey(toConversationId)
    if (fromKey === toKey) return

    const fromRecords = localEvaluationCacheRef.current[fromKey]
    if (!fromRecords || fromRecords.length === 0) return

    const toRecords = localEvaluationCacheRef.current[toKey] ?? []
    localEvaluationCacheRef.current[toKey] = mergeEvaluationRecords(toRecords, fromRecords)
    delete localEvaluationCacheRef.current[fromKey]
  }, [])

  const getCachedEvaluationRecords = useCallback((conversationId: string | null | undefined) => {
    return localEvaluationCacheRef.current[getEvaluationCacheKey(conversationId)] ?? []
  }, [])

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
      const rawToolEvent = data as Record<string, unknown>
      const consentLink = getAuthRedirectUrl(rawToolEvent)
      if (
        consentLink
        && rawToolEvent.provider === 'foundry'
        && rawToolEvent.source === 'workiq'
        && (rawToolEvent.status === 'auth_required' || rawToolEvent.status === 'consent_required')
      ) {
        if (!isAllowedAuthRedirectUrl(consentLink)) {
          setState(prev => ({
            ...prev,
            error: buildBlockedAuthRedirectError(),
            status: 'error',
            managerApprovalPolling: false,
            backgroundUpdatesPending: false,
            pendingVersion: null,
          }))
          return
        }
        window.location.assign(consentLink)
        return
      }
      setState(prev => {
        const requestedVersion = Number((data as ToolEvent).version || 0)
        const toolEvent = normalizeToolEventData(data as Record<string, unknown>, {
          fallbackVersion: Number.isFinite(requestedVersion) && requestedVersion > 0
            ? requestedVersion
            : resolveToolEventVersion(prev),
          parseSourceScope: (raw) => (raw === undefined ? undefined : parseWorkIqSourceScope(raw)),
        })
        const toolEvents = [...prev.toolEvents, toolEvent].slice(-MAX_TOOL_EVENTS)
        const workIq = applyWorkIqToolEvent(prev.workIq, toolEvent)
        const conversationSettings = workIq.workIqEnabled
          ? {
              workIqEnabled: workIq.workIqEnabled,
              workIqSourceScope: [...workIq.workIqSourceScope],
            }
          : prev.conversationSettings
        return {
          ...prev,
          toolEvents,
          conversationSettings,
          workIq,
          versions: syncLatestCompletedSnapshot(prev, {
            textContents: prev.textContents,
            images: prev.images,
            toolEvents,
            metrics: prev.metrics,
            evaluations: prev.versions[prev.versions.length - 1]?.evaluations ?? [],
          }),
        }
      })
    },
    text: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      setState(prev => {
        const textContents = [...prev.textContents, normalizeTextContentData(data as Record<string, unknown>)]
        return {
          ...prev,
          textContents,
          versions: syncLatestCompletedSnapshot(prev, {
            textContents,
            images: prev.images,
            toolEvents: prev.toolEvents,
            metrics: prev.metrics,
            evaluations: prev.versions[prev.versions.length - 1]?.evaluations ?? [],
          }),
        }
      })
    },
    image: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const image = data as ImageContent
      if (!image.url?.trim()) return
      setState(prev => {
        const images = [...prev.images, image]
        return {
          ...prev,
          images,
          versions: syncLatestCompletedSnapshot(prev, {
            textContents: prev.textContents,
            images,
            toolEvents: prev.toolEvents,
            metrics: prev.metrics,
            evaluations: prev.versions[prev.versions.length - 1]?.evaluations ?? [],
          }),
        }
      })
    },
    approval_request: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const request = data as ApprovalRequest
      migrateCachedEvaluations(stateRef.current.conversationId, request.conversation_id)
      conversationIdRef.current = request.conversation_id
      setState(prev => ({
        ...prev,
        managerApprovalPolling: request.approval_scope === 'manager',
        backgroundUpdatesPending: false,
        hasManagerApprovalPhase: prev.hasManagerApprovalPhase || request.approval_scope === 'manager',
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
      const rawError = data as Record<string, unknown>
      const consentLink = getAuthRedirectUrl(rawError)
      if (consentLink && rawError.code === 'WORKIQ_AUTH_REQUIRED') {
        if (!isAllowedAuthRedirectUrl(consentLink)) {
          setState(prev => ({
            ...prev,
            error: buildBlockedAuthRedirectError(),
            status: 'error',
            managerApprovalPolling: false,
            backgroundUpdatesPending: false,
            pendingVersion: null,
          }))
          return
        }
        window.location.assign(consentLink)
        return
      }
      setState(prev => ({
        ...prev,
        error: data as ErrorData,
        status: 'error',
        managerApprovalPolling: false,
        backgroundUpdatesPending: false,
        pendingVersion: null,
      }))
    },
    done: (data) => {
      if (requestId !== activeRequestIdRef.current) return
      const doneData = data as { conversation_id: string; metrics?: unknown; background_updates_pending?: boolean }
      const metrics = normalizePipelineMetrics(doneData.metrics)
      migrateCachedEvaluations(stateRef.current.conversationId, doneData.conversation_id)
      conversationIdRef.current = doneData.conversation_id
      setState(prev => {
        const latestSnapshot = prev.versions[prev.versions.length - 1]
        const shouldReplaceDraft = Boolean(latestSnapshot?.isDraft)
          && (!prev.pendingVersion || prev.pendingVersion.version === prev.versions.length)
        const snapshot = createArtifactSnapshot({
          textContents: prev.textContents,
          images: prev.images,
          toolEvents: prev.toolEvents,
          metrics,
          evaluations: shouldReplaceDraft ? latestSnapshot?.evaluations ?? [] : [],
        })
        const newVersions = shouldReplaceDraft
          ? [...prev.versions.slice(0, -1), snapshot]
          : [...prev.versions, snapshot]
        return {
          ...prev,
          metrics,
          status: 'completed',
          managerApprovalPolling: false,
          backgroundUpdatesPending: doneData.background_updates_pending === true,
          conversationId: doneData.conversation_id,
          versions: newVersions,
          currentVersion: newVersions.length,
          pendingVersion: null,
        }
      })
    },
  }), [migrateCachedEvaluations])

  const sendMessage = useCallback(async (message: string, options?: SendMessageOptions) => {
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    const requestId = activeRequestIdRef.current + 1
    activeRequestIdRef.current = requestId
    activeRestoreRequestIdRef.current += 1
    const previousState = stateRef.current
    const existingConversationId = conversationIdRef.current
    const currentSettings = options?.resumeState?.settings ?? stateRef.current.settings
    const currentDraftConversationSettings = options?.resumeState?.conversationSettings ?? stateRef.current.draftConversationSettings
    const nextConversationSettings = existingConversationId
      ? stateRef.current.conversationSettings
      : currentDraftConversationSettings
    const requestOptions: ChatRequestOptions | undefined = options
      ? {
          refineContext: options.refineContext,
          authInteractionMode: options.authInteractionMode,
        }
      : undefined
    const shouldPersistPendingWorkIqRequest = !existingConversationId && nextConversationSettings.workIqEnabled
    if (shouldPersistPendingWorkIqRequest) {
      savePendingWorkIqRequest({
        message,
        settings: currentSettings,
        conversationSettings: nextConversationSettings,
        options: requestOptions,
      })
    }
    const nextWorkIq = existingConversationId
      ? createWorkIqState(
          stateRef.current.conversationSettings,
          stateRef.current.workIq.status,
          stateRef.current.workIq.rawStatus,
        )
      : createWorkIqState(nextConversationSettings)
    setState(prev => ({
      ...(() => {
        const synced = ensureDraftSnapshot(syncToLatestSnapshot(prev))
        return {
          ...synced,
          status: 'running' as const,
          managerApprovalPolling: false,
          backgroundUpdatesPending: false,
          hasManagerApprovalPhase: currentSettings.managerApprovalEnabled,
          error: null,
          approvalRequest: null,
          agentProgress: null,
          conversationSettings: cloneConversationSettings(nextConversationSettings),
          workIq: nextWorkIq,
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
    try {
      const requestStartResult = await connectSSE(
        message,
        handlers,
        existingConversationId || undefined,
        controller.signal,
        currentSettings,
        nextConversationSettings,
        requestOptions,
      )
      if (requestStartResult !== 'redirecting') {
        clearPendingWorkIqRequest()
      }
      if (requestStartResult === 'redirecting' && requestId === activeRequestIdRef.current) {
        setState(previousState)
      }
    } catch (error) {
      clearPendingWorkIqRequest()
      throw error
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
    }
  }, [createHandlers])

  useEffect(() => {
    if (attemptedPendingResumeRef.current) return
    if (state.status !== 'idle' || state.conversationId || state.userMessages.length > 0) return

    attemptedPendingResumeRef.current = true
    const pendingRequest = loadPendingWorkIqRequest()
    if (!pendingRequest) return

    const restoredSettings = normalizeModelSettings(pendingRequest.settings)
    const restoredConversationSettings = cloneConversationSettings(pendingRequest.conversationSettings)
    const redirectFailure = consumeMsalRedirectFailureSentinel()

    if (redirectFailure) {
      console.warn('Skipping pending Work IQ resume after MSAL redirect failure:', redirectFailure)
      clearPendingWorkIqRequest()
      setState(prev => ({
        ...prev,
        status: 'error',
        error: {
          message: redirectFailure.message,
          code: 'WORKIQ_REDIRECT_FAILED',
        },
        settings: restoredSettings,
        conversationSettings: cloneConversationSettings(restoredConversationSettings),
        draftConversationSettings: cloneConversationSettings(restoredConversationSettings),
        workIq: createWorkIqState(restoredConversationSettings, restoredConversationSettings.workIqEnabled ? 'unavailable' : 'off', redirectFailure.stage),
      }))
      return
    }

    setState(prev => ({
      ...prev,
      settings: restoredSettings,
      conversationSettings: cloneConversationSettings(restoredConversationSettings),
      draftConversationSettings: cloneConversationSettings(restoredConversationSettings),
      workIq: createWorkIqState(restoredConversationSettings, restoredConversationSettings.workIqEnabled ? 'ready' : 'off'),
    }))

    void sendMessage(pendingRequest.message, {
      ...(pendingRequest.options ?? {}),
      authInteractionMode: 'silent',
      resumeState: {
        settings: restoredSettings,
        conversationSettings: restoredConversationSettings,
      },
    })
  }, [sendMessage, state.conversationId, state.status, state.userMessages.length])

  const approve = useCallback(async (response: string) => {
    const threadId = conversationIdRef.current
    if (!threadId) return
    const normalizedResponse = response.trim()
    const shouldAppendUserMessage = normalizedResponse.length > 0 && !isApprovalResponseText(normalizedResponse)
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller
    const requestId = activeRequestIdRef.current + 1
    activeRequestIdRef.current = requestId
    activeRestoreRequestIdRef.current += 1
    setState(prev => ({
      ...prev,
      status: 'running',
      managerApprovalPolling: false,
      backgroundUpdatesPending: false,
      approvalRequest: null,
      error: null,
      userMessages: shouldAppendUserMessage
        ? [...prev.userMessages, normalizedResponse]
        : prev.userMessages,
      pendingVersion: inferPendingVersion(prev),
    }))
    const handlers = createHandlers(requestId)
    try {
      await sendApproval(
        threadId,
        normalizedResponse,
        handlers,
        controller.signal,
        stateRef.current.workIq.workIqEnabled,
      )
    } finally {
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
    }
  }, [createHandlers])

  const reset = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    clearPendingWorkIqRequest()
    activeRequestIdRef.current += 1
    activeRestoreRequestIdRef.current += 1
    setState({
      ...initialState,
      settings: { ...DEFAULT_SETTINGS },
      conversationSettings: cloneConversationSettings(DEFAULT_CONVERSATION_SETTINGS),
      draftConversationSettings: cloneConversationSettings(DEFAULT_CONVERSATION_SETTINGS),
      workIq: createWorkIqState(DEFAULT_CONVERSATION_SETTINGS, 'off'),
    })
    conversationIdRef.current = null
    conversationEtagsRef.current = {}
    delete localEvaluationCacheRef.current[DRAFT_EVALUATION_CACHE_KEY]
  }, [])

  const startNewConversation = useCallback(() => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
    clearPendingWorkIqRequest()
    activeRequestIdRef.current += 1
    activeRestoreRequestIdRef.current += 1
    const preservedSettings = { ...stateRef.current.settings }
    const preservedConversationSettings = cloneConversationSettings(stateRef.current.draftConversationSettings)
    setState({
      ...initialState,
      settings: preservedSettings,
      conversationSettings: cloneConversationSettings(preservedConversationSettings),
      draftConversationSettings: cloneConversationSettings(preservedConversationSettings),
      workIq: createWorkIqState(preservedConversationSettings),
    })
    conversationIdRef.current = null
    conversationEtagsRef.current = {}
    delete localEvaluationCacheRef.current[DRAFT_EVALUATION_CACHE_KEY]
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
    setState(prev => ({ ...prev, settings: normalizeModelSettings(settings) }))
  }, [])

  const updateConversationSettings = useCallback((conversationSettings: ConversationSettings) => {
    setState(prev => {
      const isLocked = prev.status !== 'idle'
        || Boolean(prev.conversationId)
        || prev.userMessages.length > 0
        || prev.versions.length > 0

      if (isLocked) {
        return prev
      }

      return {
        ...prev,
        conversationSettings: cloneConversationSettings(conversationSettings),
        draftConversationSettings: cloneConversationSettings(conversationSettings),
        workIq: createWorkIqState(conversationSettings),
      }
    })
  }, [])

  const saveEvaluation = useCallback((record: EvaluationRecord) => {
    cacheEvaluationRecord(conversationIdRef.current ?? stateRef.current.conversationId, record)
    setState(prev => applyEvaluationRecord(prev, record))
  }, [cacheEvaluationRecord])

  /** 保存済み会話を復元する（新規推論を実行しない） */
  const restoreConversation = useCallback(async (conversationId: string, options?: RestoreConversationOptions) => {
    const passive = options?.passive === true
    const foregroundRequestId = activeRequestIdRef.current
    const previousState = stateRef.current
    const isCurrentConversation = stateRef.current.conversationId === conversationId

    if (passive && abortControllerRef.current) {
      return
    }

    if (!passive) {
      abortControllerRef.current?.abort()
      abortControllerRef.current = null
      activeRequestIdRef.current += 1
    }

    const restoreRequestId = activeRestoreRequestIdRef.current + 1
    activeRestoreRequestIdRef.current = restoreRequestId

    try {
      const restoreUrl = new URL(`/api/conversations/${conversationId}`, window.location.origin)
      const headers: Record<string, string> = {
        'Cache-Control': 'no-cache',
      }
      if (previousState.workIq.workIqEnabled) {
        const delegatedAuth = await getDelegatedApiAuth({ workIqRuntime: 'foundry_tool' })
        if (delegatedAuth.status !== 'ok') {
          if (!passive) {
            setState(prev => ({
              ...prev,
              status: 'error',
              error: buildWorkIqDelegatedAuthError(delegatedAuth.status),
            }))
          }
          return
        }
        Object.assign(headers, delegatedAuth.headers)
      }
      const knownEtag = isCurrentConversation ? conversationEtagsRef.current[conversationId] : undefined
      if (knownEtag) {
        headers['If-None-Match'] = knownEtag
      }
      const resp = await fetch(restoreUrl.toString(), {
        cache: 'no-store',
        headers,
      })
      if (resp.status === 304) {
        if (restoreRequestId !== activeRestoreRequestIdRef.current) return
        if (passive && activeRequestIdRef.current !== foregroundRequestId) return
        conversationIdRef.current = conversationId
        return
      }
      if (!resp.ok) return
      const nextEtag = resp.headers.get('etag')
      if (nextEtag) {
        conversationEtagsRef.current[conversationId] = nextEtag
      } else {
        delete conversationEtagsRef.current[conversationId]
      }
      const doc = await resp.json() as ConversationDocument
      if (restoreRequestId !== activeRestoreRequestIdRef.current) return
      if (passive && activeRequestIdRef.current !== foregroundRequestId) return
      if (passive && abortControllerRef.current) return

      const restoredState = mergeCachedEvaluationsIntoState(
        buildRestoredPipelineState(doc, conversationId, stateRef.current.settings),
        getCachedEvaluationRecords(conversationId),
      )

      const nextState = preserveViewedCommittedVersion(previousState, {
        ...restoredState,
        draftConversationSettings: cloneConversationSettings(previousState.draftConversationSettings),
      }, passive)

      setState(nextState)

      conversationIdRef.current = conversationId
    } catch (err) {
      console.warn('会話の復元に失敗:', err)
    }
  }, [getCachedEvaluationRecords])

  return {
    state,
    sendMessage,
    approve,
    reset,
    startNewConversation,
    restoreVersion,
    updateSettings,
    updateConversationSettings,
    restoreConversation,
    saveEvaluation,
  }
}
