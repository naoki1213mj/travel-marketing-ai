import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EvaluationPanel } from './EvaluationPanel'

const originalFetch = globalThis.fetch

function createJsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('EvaluationPanel', () => {
  const mockFetch = vi.fn()
  const t = (key: string) => key

  beforeEach(() => {
    globalThis.fetch = mockFetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('keeps evaluation history separated by artifact version', async () => {
    mockFetch.mockResolvedValueOnce(createJsonResponse({
      builtin: { relevance: { score: 4, reason: 'good' } },
    }))

    const { rerender } = render(
      <EvaluationPanel query="q" response="plan A" html="<p>A</p>" t={t} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'eval.run' }))

    await waitFor(() => {
      expect(screen.getByText('4.0')).not.toBeNull()
    })

    rerender(<EvaluationPanel query="q" response="plan B" html="<p>B</p>" t={t} />)
    expect(screen.queryByText('4.0')).toBeNull()

    mockFetch.mockResolvedValueOnce(createJsonResponse({
      builtin: { relevance: { score: 2, reason: 'weak' } },
    }))

    fireEvent.click(screen.getByRole('button', { name: 'eval.run' }))

    await waitFor(() => {
      expect(screen.getByText('2.0')).not.toBeNull()
    })

    rerender(<EvaluationPanel query="q" response="plan A" html="<p>A</p>" t={t} />)

    expect(screen.getByText('4.0')).not.toBeNull()
    expect(screen.queryByText('2.0')).toBeNull()
  })

  it('keeps evaluation visible when brochure html arrives later', async () => {
    mockFetch.mockResolvedValueOnce(createJsonResponse({
      builtin: { relevance: { score: 4, reason: 'good' } },
    }))

    const { rerender } = render(
      <EvaluationPanel query="q" response="plan A" html="" t={t} />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'eval.run' }))

    await waitFor(() => {
      expect(screen.getByText('4.0')).not.toBeNull()
    })

    rerender(<EvaluationPanel query="q" response="plan A" html="<p>brochure ready</p>" t={t} />)

    expect(screen.getByText('4.0')).not.toBeNull()
  })
})
