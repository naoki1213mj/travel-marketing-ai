import { describe, expect, it } from 'vitest'
import type { EvidenceItem } from './event-schemas'
import type { ToolEvent } from './tool-events'
import { classifyEvidence, classifyToolEvent, collectActiveIQBrands, hasIQAttempted } from './iq-brand'

function ev(overrides: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    id: 'e1',
    title: 't',
    source: 'fabric',
    ...overrides,
  } as EvidenceItem
}

function te(overrides: Partial<ToolEvent> = {}): ToolEvent {
  return {
    id: 't1',
    tool: 'query_data_agent',
    status: 'completed',
    agent: 'data-search-agent',
    version: 1,
    ...overrides,
  } as ToolEvent
}

describe('classifyEvidence', () => {
  it('returns fabric_iq for fabric source', () => {
    expect(classifyEvidence(ev({ source: 'fabric' }))).toBe('fabric_iq')
    expect(classifyEvidence(ev({ source: 'fabric_data_agent' }))).toBe('fabric_iq')
    expect(classifyEvidence(ev({ source: 'fabric_sql' }))).toBe('fabric_iq')
  })

  it('returns foundry_iq for foundry/azure_ai_search source', () => {
    expect(classifyEvidence(ev({ source: 'foundry_iq' }))).toBe('foundry_iq')
    expect(classifyEvidence(ev({ source: 'azure_ai_search' }))).toBe('foundry_iq')
    expect(classifyEvidence(ev({ source: 'foundry' }))).toBe('foundry_iq')
  })

  it('returns work_iq for workiq source (B2 evidence cards)', () => {
    expect(classifyEvidence(ev({ source: 'workiq' }))).toBe('work_iq')
    expect(classifyEvidence(ev({ source: 'work_iq' }))).toBe('work_iq')
  })

  it('returns null for local / fallback / web (silent fallback signals)', () => {
    expect(classifyEvidence(ev({ source: 'local' }))).toBeNull()
    expect(classifyEvidence(ev({ source: 'local-check' }))).toBeNull()
    expect(classifyEvidence(ev({ source: 'fallback' }))).toBeNull()
    expect(classifyEvidence(ev({ source: 'web' }))).toBeNull()
  })
})

describe('classifyToolEvent', () => {
  it('classifies query_data_agent → fabric_iq when completed', () => {
    expect(classifyToolEvent(te({ tool: 'query_data_agent' }))).toBe('fabric_iq')
  })

  it('returns null when query_data_agent failed or fell back', () => {
    expect(classifyToolEvent(te({ tool: 'query_data_agent', status: 'failed' }))).toBeNull()
    expect(classifyToolEvent(te({ tool: 'query_data_agent', fallback: 'csv' }))).toBeNull()
  })

  it('classifies foundry_iq_search and search_knowledge_base → foundry_iq', () => {
    expect(classifyToolEvent(te({ tool: 'foundry_iq_search', agent: 'regulation-check-agent' }))).toBe('foundry_iq')
    expect(classifyToolEvent(te({ tool: 'search_knowledge_base', agent: 'regulation-check-agent' }))).toBe('foundry_iq')
  })

  it('does NOT claim foundry_iq for generic web search / market trends / safety info (rubber-duck #1)', () => {
    expect(classifyToolEvent(te({ tool: 'web_search', agent: 'marketing-plan-agent' }))).toBeNull()
    expect(classifyToolEvent(te({ tool: 'search_market_trends', agent: 'marketing-plan-agent' }))).toBeNull()
    expect(classifyToolEvent(te({ tool: 'search_safety_info', agent: 'regulation-check-agent' }))).toBeNull()
  })

  it('classifies workiq_foundry_tool → work_iq', () => {
    expect(classifyToolEvent(te({ tool: 'workiq_foundry_tool', agent: 'marketing-plan-agent' }))).toBe('work_iq')
    expect(classifyToolEvent(te({ tool: 'generate_workplace_context_brief', agent: 'marketing-plan-agent' }))).toBe('work_iq')
  })

  it('classifies search_sales_history based on evidence source', () => {
    const fabricEvent = te({
      tool: 'search_sales_history',
      agent: 'data-search-agent',
      evidence: [ev({ source: 'fabric' })],
    } as Partial<ToolEvent>)
    expect(classifyToolEvent(fabricEvent)).toBe('fabric_iq')

    const localEvent = te({
      tool: 'search_sales_history',
      agent: 'data-search-agent',
      evidence: [ev({ source: 'local' })],
    } as Partial<ToolEvent>)
    expect(classifyToolEvent(localEvent)).toBeNull()
  })
})

describe('collectActiveIQBrands', () => {
  it('aggregates unique IQ brands across tool and evidence', () => {
    const events: ToolEvent[] = [
      te({ tool: 'query_data_agent', agent: 'data-search-agent' }),
      te({ tool: 'foundry_iq_search', agent: 'regulation-check-agent' }),
      te({ tool: 'workiq_foundry_tool', agent: 'marketing-plan-agent' }),
    ]
    const brands = collectActiveIQBrands(events)
    expect(brands.has('fabric_iq')).toBe(true)
    expect(brands.has('foundry_iq')).toBe(true)
    expect(brands.has('work_iq')).toBe(true)
    expect(brands.size).toBe(3)
  })

  it('does not include IQ brands from failed/fallback tools, including their evidence (rubber-duck #4)', () => {
    const events: ToolEvent[] = [
      te({ tool: 'query_data_agent', status: 'failed', evidence: [ev({ source: 'fabric' })] } as Partial<ToolEvent>),
      te({ tool: 'foundry_iq_search', fallback: 'static', evidence: [ev({ source: 'foundry_iq' })] } as Partial<ToolEvent>),
    ]
    const brands = collectActiveIQBrands(events)
    expect(brands.size).toBe(0)
  })
})

describe('hasIQAttempted (rubber-duck pr1-impl-critique non-blocking #3)', () => {
  it('returns true for fabric_iq when query_data_agent fired (even on failure)', () => {
    const events: ToolEvent[] = [
      te({ tool: 'query_data_agent', status: 'failed' } as Partial<ToolEvent>),
    ]
    expect(hasIQAttempted(events, 'fabric_iq')).toBe(true)
  })

  it('returns true for fabric_iq when search_sales_history fired (even with no Fabric evidence)', () => {
    const events: ToolEvent[] = [
      te({ tool: 'search_sales_history', status: 'completed', evidence: [] } as Partial<ToolEvent>),
    ]
    expect(hasIQAttempted(events, 'fabric_iq')).toBe(true)
  })

  it('returns true for foundry_iq when search_knowledge_base fired (even on fallback)', () => {
    const events: ToolEvent[] = [
      te({ tool: 'search_knowledge_base', fallback: 'static', agent: 'regulation-check-agent' } as Partial<ToolEvent>),
    ]
    expect(hasIQAttempted(events, 'foundry_iq')).toBe(true)
  })

  it('returns true for work_iq when workiq_foundry_tool fired', () => {
    const events: ToolEvent[] = [
      te({ tool: 'workiq_foundry_tool', status: 'completed', agent: 'marketing-plan-agent' } as Partial<ToolEvent>),
    ]
    expect(hasIQAttempted(events, 'work_iq')).toBe(true)
  })

  it('returns false when no tool from the IQ family was ever attempted', () => {
    const events: ToolEvent[] = [
      te({ tool: 'search_market_trends', status: 'completed' } as Partial<ToolEvent>),
    ]
    expect(hasIQAttempted(events, 'fabric_iq')).toBe(false)
    expect(hasIQAttempted(events, 'foundry_iq')).toBe(false)
    expect(hasIQAttempted(events, 'work_iq')).toBe(false)
  })

  it('returns false for empty event list', () => {
    expect(hasIQAttempted([], 'fabric_iq')).toBe(false)
  })
})
