import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ToolEvent } from '../hooks/useSSE'
import { ToolEventBadges } from './ToolEventBadges'

const t = (key: string) => ({
  'tool.source.foundry': 'Microsoft Foundry',
  'tool.source.workiq': 'Work IQ',
  'tool.meta.inferred': 'Inferred',
  'tool.status.auth_required': 'Sign-in required',
  'tool.status.consent_required': 'Consent required',
  'tool.status.unavailable': 'Unavailable',
  'tool.status.timeout': 'Timed out',
  'tool.status.identity_mismatch': 'Identity mismatch',
  'settings.workiq.source.meeting_notes': 'Meeting notes',
  'settings.workiq.source.emails': 'Emails',
  'settings.workiq.source.teams_chats': 'Teams chats',
  'settings.workiq.source.documents_notes': 'Documents / notes',
}[key] ?? key)

describe('ToolEventBadges', () => {
  it('highlights canonical Foundry Work IQ tool events with a dedicated badge', () => {
    const events: ToolEvent[] = [
      {
        tool: 'workiq_foundry_tool',
        status: 'completed',
        agent: 'marketing-plan-agent',
        source: 'workiq',
        provider: 'foundry',
        display_name: 'Work IQ context tools',
      },
    ]

    const { container } = render(<ToolEventBadges events={events} t={t} />)

    expect(screen.getByText('Work IQ context tools')).toBeInTheDocument()
    expect(screen.getByText('Microsoft Foundry Work IQ')).toBeInTheDocument()
    expect(container.querySelector('[data-tool-kind="foundry-workiq"]')).not.toBeNull()
    expect(container.querySelector('[data-tool-provider="foundry"]')).not.toBeNull()
  })

  it('shows Work IQ source and status badges', () => {
    const events: ToolEvent[] = [
      {
        tool: 'fetch_workplace_context',
        status: 'auth_required',
        agent: 'marketing-plan-agent',
        source: 'workiq',
      },
    ]

    const { container } = render(<ToolEventBadges events={events} t={t} />)

    expect(screen.getByText('fetch workplace context')).toBeInTheDocument()
    expect(screen.getByText('Work IQ')).toBeInTheDocument()
    expect(screen.getByText('Sign-in required')).toBeInTheDocument()
    expect(container.querySelector('[data-tool-provider="workiq"]')).not.toBeNull()
  })

  it('renders Work IQ source scope labels when provided', () => {
    const events: ToolEvent[] = [
      {
        tool: 'fetch_workplace_context',
        status: 'completed',
        agent: 'marketing-plan-agent',
        source: 'workiq',
        source_scope: ['meeting_notes', 'emails'],
      },
    ]

    render(<ToolEventBadges events={events} t={t} />)

    expect(screen.getByText('Meeting notes')).toBeInTheDocument()
    expect(screen.getByText('Emails')).toBeInTheDocument()
  })

  it('collapses running and completed events for the same tool into one completed badge', () => {
    const events: ToolEvent[] = [
      {
        tool: 'fetch_workplace_context',
        status: 'running',
        agent: 'marketing-plan-agent',
        source: 'workiq',
        started_at: '2026-04-19T05:00:00Z',
      },
      {
        tool: 'fetch_workplace_context',
        status: 'completed',
        agent: 'marketing-plan-agent',
        source: 'workiq',
        started_at: '2026-04-19T05:00:00Z',
        finished_at: '2026-04-19T05:00:02Z',
      },
    ]

    const { container } = render(<ToolEventBadges events={events} t={t} />)

    expect(container.querySelectorAll('[data-tool-name="fetch_workplace_context"]')).toHaveLength(1)
    expect(container.querySelector('[data-tool-name="fetch_workplace_context"][data-tool-status="completed"]')).not.toBeNull()
  })
})
