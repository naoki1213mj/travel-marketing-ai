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
})
