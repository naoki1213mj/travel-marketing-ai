import type { WorkIqSourceScope } from '../components/SettingsPanel'

export interface ToolEvent {
  event_id?: string
  tool: string
  status: string
  agent: string
  source?: string
  provider?: string
  display_name?: string
  fallback?: string
  version?: number
  round?: number
  step?: number
  step_key?: string
  phase?: string
  inferred?: boolean
  background_update?: boolean
  started_at?: string
  finished_at?: string
  duration_ms?: number
  error_code?: string
  error_message?: string
  source_scope?: WorkIqSourceScope[]
}

const TOOL_ALIASES: Record<string, string> = {
  search_knowledge_base: 'foundry_iq_search',
}

const AGENT_STEP_KEYS: Record<string, string> = {
  'data-search-agent': 'data-search-agent',
  'marketing-plan-agent': 'marketing-plan-agent',
  'regulation-check-agent': 'regulation-check-agent',
  'plan-revision-agent': 'regulation-check-agent',
  'brochure-gen-agent': 'brochure-gen-agent',
  'video-gen-agent': 'video-gen-agent',
  'quality-review-agent': 'quality-review-agent',
  'improvement-mcp': 'marketing-plan-agent',
}

function toPositiveNumber(value: unknown): number | undefined {
  const parsed = Number(value || 0)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined
}

function toBoolean(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase()
    if (normalized === 'true') return true
    if (normalized === 'false') return false
  }
  return undefined
}

export function normalizeToolName(tool: string): string {
  const normalized = tool.trim()
  return TOOL_ALIASES[normalized] ?? normalized
}

export function resolveToolStepKey(agent: string, stepKey?: string): string {
  const normalizedStepKey = String(stepKey || '').trim()
  if (normalizedStepKey) return normalizedStepKey
  const normalizedAgent = String(agent || '').trim()
  return AGENT_STEP_KEYS[normalizedAgent] ?? normalizedAgent
}

export function resolveToolProvider(event: Pick<ToolEvent, 'provider' | 'source' | 'tool' | 'agent'>): string {
  const normalizedProvider = String(event.provider || '').trim()
  if (normalizedProvider) return normalizedProvider

  const normalizedSource = String(event.source || '').trim()
  if (normalizedSource) return normalizedSource

  if (event.agent === 'improvement-mcp') return 'mcp'
  if (['web_search', 'code_interpreter', 'foundry_iq_search'].includes(event.tool)) return 'foundry'
  return 'local'
}

export function isToolAttentionStatus(status: string): boolean {
  return new Set([
    'failed',
    'auth_required',
    'consent_required',
    'identity_mismatch',
    'timeout',
    'unavailable',
    'error',
  ]).has(status)
}

export function normalizeToolEventData(
  data: Record<string, unknown>,
  options: {
    fallbackVersion: number
    parseSourceScope: (raw: unknown) => WorkIqSourceScope[] | undefined
  },
): ToolEvent {
  const tool = normalizeToolName(String(data.tool || ''))
  const agent = String(data.agent || '')
  const provider = String(data.provider || '').trim() || undefined
  const source = String(data.source || '').trim() || undefined

  return {
    event_id: String(data.event_id || '').trim() || undefined,
    tool,
    status: String(data.status || ''),
    agent,
    source,
    provider,
    display_name: String(data.display_name || '').trim() || undefined,
    fallback: String(data.fallback || '').trim() || undefined,
    version: toPositiveNumber(data.version) ?? options.fallbackVersion,
    round: toPositiveNumber(data.round),
    step: toPositiveNumber(data.step),
    step_key: resolveToolStepKey(agent, String(data.step_key || '')),
    phase: String(data.phase || '').trim() || undefined,
    inferred: toBoolean(data.inferred),
    background_update: toBoolean(data.background_update),
    started_at: String(data.started_at || '').trim() || undefined,
    finished_at: String(data.finished_at || '').trim() || undefined,
    duration_ms: toPositiveNumber(data.duration_ms),
    error_code: String(data.error_code || '').trim() || undefined,
    error_message: String(data.error_message || '').trim() || undefined,
    source_scope: options.parseSourceScope(data.source_scope),
  }
}
