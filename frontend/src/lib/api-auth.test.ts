import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { getDelegatedApiAuth, resetDelegatedApiAuthCache } from './api-auth'

const originalFetch = global.fetch
const {
  getWorkIqFoundryAuth,
  getWorkIqGraphAuth,
} = vi.hoisted(() => ({
  getWorkIqFoundryAuth: vi.fn(),
  getWorkIqGraphAuth: vi.fn(),
}))

vi.mock('./msal-auth', () => ({
  getWorkIqFoundryAuth,
  getWorkIqGraphAuth,
}))

describe('getDelegatedApiAuth', () => {
  beforeEach(() => {
    resetDelegatedApiAuthCache()
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ client_id: 'client-id', tenant_id: 'tenant-id' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ) as typeof fetch
    getWorkIqFoundryAuth.mockReset()
    getWorkIqGraphAuth.mockReset()
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('uses connector-scoped auth for foundry_tool and adds graph fallback header when available', async () => {
    getWorkIqFoundryAuth.mockResolvedValue({ token: 'foundry-token', status: 'ok' })
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    const result = await getDelegatedApiAuth({ interactive: true, workIqRuntime: 'foundry_tool' })

    expect(getWorkIqFoundryAuth).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' }, true)
    expect(getWorkIqGraphAuth).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' }, false)
    expect(result).toEqual({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
  })

  it('uses graph auth for graph_prefetch runtime', async () => {
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    const result = await getDelegatedApiAuth({ interactive: false, workIqRuntime: 'graph_prefetch' })

    expect(getWorkIqFoundryAuth).not.toHaveBeenCalled()
    expect(getWorkIqGraphAuth).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' }, false)
    expect(result).toEqual({
      headers: { Authorization: 'Bearer graph-token' },
      status: 'ok',
    })
  })

  it('defaults to foundry auth when runtime is omitted', async () => {
    getWorkIqFoundryAuth.mockResolvedValue({ token: 'foundry-token', status: 'ok' })
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    const result = await getDelegatedApiAuth({ interactive: false })

    expect(getWorkIqFoundryAuth).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' }, false)
    expect(getWorkIqGraphAuth).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' }, false)
    expect(result).toEqual({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
  })
})
