import { describe, expect, it } from 'vitest'
import { normalizeToolEventData } from './tool-events'

describe('normalizeToolEventData schema extensions', () => {
  it('preserves optional evidence, chart, trace, debug, source metadata, and ingestion fields', () => {
    const event = normalizeToolEventData(
      {
        tool: 'web_search',
        status: 'completed',
        agent: 'marketing-plan-agent',
        evidence: [{ source: 'web', url: 'https://example.com/report', relevance: 0.8 }],
        charts: [{ chart_type: 'bar', title: '需要' }],
        trace_events: [{ name: 'search.call', duration_ms: 20 }],
        debug_events: [{ message: 'cache hit', level: 'info' }],
        source_metadata: [{ source: 'meeting_notes', count: 2, connector: 'teams' }],
        source_ingestion: [{ source: 'fabric', status: 'completed', items_ingested: 10 }],
      },
      {
        fallbackVersion: 1,
        parseSourceScope: () => undefined,
      },
    )

    expect(event.provider).toBeUndefined()
    expect(event.evidence?.[0].url).toBe('https://example.com/report')
    expect(event.charts?.[0].chart_type).toBe('bar')
    expect(event.trace_events?.[0].name).toBe('search.call')
    expect(event.debug_events?.[0].level).toBe('info')
    expect(event.source_metadata?.[0].connector).toBe('teams')
    expect(event.source_ingestion?.[0].items_ingested).toBe(10)
  })
})
