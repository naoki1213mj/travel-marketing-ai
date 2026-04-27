import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchCapabilities, isCapabilityAvailable, normalizeCapabilities } from './capabilities'

const originalFetch = global.fetch

describe('capabilities client', () => {
  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('normalizes backend capability responses', () => {
    const snapshot = normalizeCapabilities({
      version: 1,
      features: {
        gpt_55: { available: true, configured: true },
        malformed: { available: 'yes' },
      },
    })

    expect(snapshot?.features.gpt_55?.available).toBe(true)
    expect(isCapabilityAvailable(snapshot, 'gpt_55')).toBe(true)
    expect(isCapabilityAvailable(snapshot, 'work_iq')).toBeNull()
  })

  it('returns null for malformed responses', () => {
    expect(normalizeCapabilities({ features: null })).toBeNull()
    expect(normalizeCapabilities(null)).toBeNull()
  })

  it('fetches /api/capabilities and hides network failures', async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({
        version: 1,
        features: {
          work_iq: { available: false, configured: false },
        },
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ) as typeof fetch

    const snapshot = await fetchCapabilities()

    expect(global.fetch).toHaveBeenCalledWith('/api/capabilities')
    expect(isCapabilityAvailable(snapshot, 'work_iq')).toBe(false)
  })

  it('returns null on non-OK responses', async () => {
    global.fetch = vi.fn(async () => new Response('', { status: 503 })) as typeof fetch

    await expect(fetchCapabilities()).resolves.toBeNull()
  })
})
