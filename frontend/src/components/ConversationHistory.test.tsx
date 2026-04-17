import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ConversationHistory } from './ConversationHistory'

const originalFetch = globalThis.fetch
const { getDelegatedApiHeaders } = vi.hoisted(() => ({
  getDelegatedApiHeaders: vi.fn(async () => ({})),
}))

vi.mock('../lib/api-auth', () => ({
  getDelegatedApiHeaders,
}))

const t = (key: string) => ({
  'history.title': '会話履歴',
  'history.close': '閉じる',
  'history.empty': '履歴はありません',
  'history.no_input': '入力なし',
}[key] ?? key)

describe('ConversationHistory', () => {
  beforeEach(() => {
    globalThis.fetch = vi.fn()
    getDelegatedApiHeaders.mockReset()
    getDelegatedApiHeaders.mockResolvedValue({})
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('reuses the list ETag and preserves history on 304 responses', async () => {
    vi.mocked(globalThis.fetch)
      .mockResolvedValueOnce(new Response(JSON.stringify({
        conversations: [
          {
            id: 'conv-1',
            input: '沖縄プラン',
            status: 'completed',
            created_at: '2026-04-05T00:00:00+00:00',
          },
        ],
      }), { headers: { ETag: 'W/"history-1"' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { ETag: 'W/"history-1"' } }))

    render(
      <ConversationHistory
        onSelect={vi.fn()}
        t={t}
        locale="ja-JP"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '会話履歴' }))

    await waitFor(() => {
      expect(screen.getByText('沖縄プラン')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: '閉じる' }))
    fireEvent.click(screen.getByRole('button', { name: '会話履歴' }))

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(2)
    })

    const [, secondOptions] = vi.mocked(globalThis.fetch).mock.calls[1]
    expect(secondOptions).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
        'If-None-Match': 'W/"history-1"',
      },
    })
    expect(screen.getByText('沖縄プラン')).toBeInTheDocument()
  })

  it('adds delegated auth headers when available', async () => {
    getDelegatedApiHeaders.mockResolvedValue({ Authorization: 'Bearer delegated-token' })
    vi.mocked(globalThis.fetch).mockResolvedValue(
      new Response(JSON.stringify({ conversations: [] }), { headers: { ETag: 'W/"history-2"' } }),
    )

    render(
      <ConversationHistory
        onSelect={vi.fn()}
        t={t}
        locale="ja-JP"
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '会話履歴' }))

    await waitFor(() => {
      expect(globalThis.fetch).toHaveBeenCalledTimes(1)
    })

    const [, options] = vi.mocked(globalThis.fetch).mock.calls[0]
    expect(options).toMatchObject({
      cache: 'no-store',
      headers: {
        'Cache-Control': 'no-cache',
        Authorization: 'Bearer delegated-token',
      },
    })
  })
})
