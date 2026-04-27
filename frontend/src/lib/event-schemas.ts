export type JsonScalar = string | number | boolean | null
export type JsonValue = JsonScalar | JsonObject | JsonValue[]

export interface JsonObject {
  [key: string]: JsonValue
}

export interface EvidenceItem {
  id?: string
  title?: string
  source: string
  url?: string
  quote?: string
  relevance?: number
  retrieved_at?: string
  metadata?: Record<string, JsonScalar>
}

export interface ChartSpec {
  chart_type: 'bar' | 'line' | 'area' | 'pie' | 'scatter' | 'table' | 'kpi' | 'mixed'
  title?: string
  x_label?: string
  y_label?: string
  series?: string[]
  data?: Record<string, JsonScalar>[]
  metadata?: Record<string, JsonScalar>
}

export interface TraceEvent {
  event_id?: string
  name: string
  phase?: string
  status?: string
  timestamp?: string
  agent?: string
  tool?: string
  duration_ms?: number
  metadata?: Record<string, JsonScalar>
}

export interface DebugEvent {
  event_id?: string
  level: 'debug' | 'info' | 'warning' | 'error'
  message: string
  code?: string
  timestamp?: string
  agent?: string
  metadata?: Record<string, JsonScalar>
}

export interface WorkIqSourceMetadata {
  source: string
  label?: string
  count?: number
  connector?: string
  status?: string
  confidence?: number
  latest_timestamp?: string
  evidence_ids?: string[]
}

export interface SourceIngestionState {
  source: string
  status: 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'skipped' | 'unknown'
  run_id?: string
  items_discovered?: number
  items_ingested?: number
  items_failed?: number
  last_ingested_at?: string
  error_code?: string
  error_message?: string
}

export interface PipelineMetrics {
  latency_seconds: number
  tool_calls: number
  total_tokens: number
  prompt_tokens?: number
  completion_tokens?: number
  estimated_cost_usd?: number
  retry_count?: number
  cache_hits?: number
  cache_misses?: number
  agent_latencies?: Record<string, number>
  tool_latencies?: Record<string, number>
  evidence?: EvidenceItem[]
  charts?: ChartSpec[]
  trace_events?: TraceEvent[]
  debug_events?: DebugEvent[]
  source_ingestion?: SourceIngestionState[]
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function asRecords(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) {
    return value.filter(isRecord)
  }
  return isRecord(value) ? [value] : []
}

function toTrimmedString(value: unknown): string | undefined {
  if (value === null || value === undefined) return undefined
  const normalized = String(value).trim()
  return normalized || undefined
}

function toNonNegativeNumber(value: unknown): number | undefined {
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined
}

function toUnitNumber(value: unknown): number | undefined {
  const parsed = toNonNegativeNumber(value)
  return parsed !== undefined && parsed <= 1 ? parsed : undefined
}

function toHttpUrl(value: unknown): string | undefined {
  const rawUrl = toTrimmedString(value)
  if (!rawUrl) return undefined
  try {
    const parsed = new URL(rawUrl)
    return parsed.protocol === 'https:' || parsed.protocol === 'http:' ? rawUrl : undefined
  } catch {
    return undefined
  }
}

function normalizeScalar(value: unknown): JsonScalar | undefined {
  if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return value
  }
  return undefined
}

function normalizeScalarRecord(value: unknown): Record<string, JsonScalar> | undefined {
  if (!isRecord(value)) return undefined
  const normalized: Record<string, JsonScalar> = {}
  Object.entries(value).forEach(([key, item]) => {
    const scalar = normalizeScalar(item)
    if (scalar !== undefined) {
      normalized[key] = scalar
    }
  })
  return Object.keys(normalized).length > 0 ? normalized : undefined
}

function normalizeNumberRecord(value: unknown): Record<string, number> | undefined {
  if (!isRecord(value)) return undefined
  const normalized: Record<string, number> = {}
  Object.entries(value).forEach(([key, item]) => {
    const parsed = toNonNegativeNumber(item)
    if (parsed !== undefined) {
      normalized[key] = parsed
    }
  })
  return Object.keys(normalized).length > 0 ? normalized : undefined
}

function normalizeStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  const normalized = value
    .map(toTrimmedString)
    .filter((item): item is string => item !== undefined)
  return normalized.length > 0 ? [...new Set(normalized)] : undefined
}

function compactRecord<T extends Record<string, unknown>>(record: T): T {
  return Object.fromEntries(
    Object.entries(record).filter(([, value]) => value !== undefined),
  ) as T
}

export function normalizeEvidenceItems(value: unknown): EvidenceItem[] | undefined {
  const normalized = asRecords(value)
    .map((item): EvidenceItem | null => {
      const source = toTrimmedString(item.source)
      if (!source) return null
      return compactRecord({
        id: toTrimmedString(item.id),
        title: toTrimmedString(item.title),
        source,
        url: toHttpUrl(item.url),
        quote: toTrimmedString(item.quote),
        relevance: toUnitNumber(item.relevance),
        retrieved_at: toTrimmedString(item.retrieved_at),
        metadata: normalizeScalarRecord(item.metadata),
      })
    })
    .filter((item): item is EvidenceItem => item !== null)
  return normalized.length > 0 ? normalized : undefined
}

function normalizeChartType(value: unknown): ChartSpec['chart_type'] {
  const normalized = toTrimmedString(value)?.toLowerCase()
  switch (normalized) {
    case 'bar':
    case 'line':
    case 'area':
    case 'pie':
    case 'scatter':
    case 'kpi':
    case 'mixed':
    case 'table':
      return normalized
    default:
      return 'table'
  }
}

export function normalizeChartSpecs(value: unknown): ChartSpec[] | undefined {
  const normalized = asRecords(value).map((item): ChartSpec => {
    const data = asRecords(item.data)
      .map(normalizeScalarRecord)
      .filter((row): row is Record<string, JsonScalar> => row !== undefined)
    return compactRecord({
      chart_type: normalizeChartType(item.chart_type ?? item.type),
      title: toTrimmedString(item.title),
      x_label: toTrimmedString(item.x_label),
      y_label: toTrimmedString(item.y_label),
      series: normalizeStringArray(item.series),
      data: data.length > 0 ? data : undefined,
      metadata: normalizeScalarRecord(item.metadata),
    })
  })
  return normalized.length > 0 ? normalized : undefined
}

export function normalizeTraceEvents(value: unknown): TraceEvent[] | undefined {
  const normalized = asRecords(value)
    .map((item): TraceEvent | null => {
      const name = toTrimmedString(item.name)
      if (!name) return null
      return compactRecord({
        event_id: toTrimmedString(item.event_id),
        name,
        phase: toTrimmedString(item.phase),
        status: toTrimmedString(item.status),
        timestamp: toTrimmedString(item.timestamp),
        agent: toTrimmedString(item.agent),
        tool: toTrimmedString(item.tool),
        duration_ms: toNonNegativeNumber(item.duration_ms),
        metadata: normalizeScalarRecord(item.metadata),
      })
    })
    .filter((item): item is TraceEvent => item !== null)
  return normalized.length > 0 ? normalized : undefined
}

function normalizeDebugLevel(value: unknown): DebugEvent['level'] {
  const normalized = toTrimmedString(value)?.toLowerCase()
  switch (normalized) {
    case 'info':
    case 'warning':
    case 'error':
      return normalized
    default:
      return 'debug'
  }
}

export function normalizeDebugEvents(value: unknown): DebugEvent[] | undefined {
  const normalized = asRecords(value)
    .map((item): DebugEvent | null => {
      const message = toTrimmedString(item.message)
      if (!message) return null
      return compactRecord({
        event_id: toTrimmedString(item.event_id),
        level: normalizeDebugLevel(item.level),
        message,
        code: toTrimmedString(item.code),
        timestamp: toTrimmedString(item.timestamp),
        agent: toTrimmedString(item.agent),
        metadata: normalizeScalarRecord(item.metadata),
      })
    })
    .filter((item): item is DebugEvent => item !== null)
  return normalized.length > 0 ? normalized : undefined
}

export function normalizeWorkIqSourceMetadata(value: unknown): WorkIqSourceMetadata[] | undefined {
  const normalized = asRecords(value)
    .map((item): WorkIqSourceMetadata | null => {
      const source = toTrimmedString(item.source)
      if (!source) return null
      return compactRecord({
        source,
        label: toTrimmedString(item.label),
        count: toNonNegativeNumber(item.count),
        connector: toTrimmedString(item.connector),
        status: toTrimmedString(item.status),
        confidence: toUnitNumber(item.confidence),
        latest_timestamp: toTrimmedString(item.latest_timestamp),
        evidence_ids: normalizeStringArray(item.evidence_ids),
      })
    })
    .filter((item): item is WorkIqSourceMetadata => item !== null)
  return normalized.length > 0 ? normalized : undefined
}

function normalizeIngestionStatus(value: unknown): SourceIngestionState['status'] {
  const normalized = toTrimmedString(value)?.toLowerCase()
  switch (normalized) {
    case 'pending':
    case 'running':
    case 'completed':
    case 'partial':
    case 'failed':
    case 'skipped':
      return normalized
    default:
      return 'unknown'
  }
}

export function normalizeSourceIngestionStates(value: unknown): SourceIngestionState[] | undefined {
  const normalized = asRecords(value)
    .map((item): SourceIngestionState | null => {
      const source = toTrimmedString(item.source)
      if (!source) return null
      return compactRecord({
        source,
        status: normalizeIngestionStatus(item.status),
        run_id: toTrimmedString(item.run_id),
        items_discovered: toNonNegativeNumber(item.items_discovered),
        items_ingested: toNonNegativeNumber(item.items_ingested),
        items_failed: toNonNegativeNumber(item.items_failed),
        last_ingested_at: toTrimmedString(item.last_ingested_at),
        error_code: toTrimmedString(item.error_code),
        error_message: toTrimmedString(item.error_message),
      })
    })
    .filter((item): item is SourceIngestionState => item !== null)
  return normalized.length > 0 ? normalized : undefined
}

export function normalizePipelineMetrics(value: unknown): PipelineMetrics | null {
  if (!isRecord(value)) return null
  return compactRecord({
    latency_seconds: toNonNegativeNumber(value.latency_seconds) ?? 0,
    tool_calls: toNonNegativeNumber(value.tool_calls) ?? 0,
    total_tokens: toNonNegativeNumber(value.total_tokens) ?? 0,
    prompt_tokens: toNonNegativeNumber(value.prompt_tokens),
    completion_tokens: toNonNegativeNumber(value.completion_tokens),
    estimated_cost_usd: toNonNegativeNumber(value.estimated_cost_usd),
    retry_count: toNonNegativeNumber(value.retry_count),
    cache_hits: toNonNegativeNumber(value.cache_hits),
    cache_misses: toNonNegativeNumber(value.cache_misses),
    agent_latencies: normalizeNumberRecord(value.agent_latencies ?? value.latency_by_agent_seconds),
    tool_latencies: normalizeNumberRecord(value.tool_latencies),
    evidence: normalizeEvidenceItems(value.evidence),
    charts: normalizeChartSpecs(value.charts),
    trace_events: normalizeTraceEvents(value.trace_events),
    debug_events: normalizeDebugEvents(value.debug_events),
    source_ingestion: normalizeSourceIngestionStates(value.source_ingestion),
  })
}
