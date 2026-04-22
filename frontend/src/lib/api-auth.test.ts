import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { bootstrapDelegatedApiAuth, getDelegatedApiAuth, resetDelegatedApiAuthCache } from './api-auth'
import { MSAL_CONFIG_CACHE_KEY } from './msal-config-cache'

const originalFetch = global.fetch
const {
  getWorkIqFoundryAuth,
  getWorkIqGraphAuth,
  initMsal,
} = vi.hoisted(() => ({
  getWorkIqFoundryAuth: vi.fn(),
  getWorkIqGraphAuth: vi.fn(),
  initMsal: vi.fn(async () => {}),
}))

vi.mock('./msal-auth', () => ({
  getWorkIqFoundryAuth,
  getWorkIqGraphAuth,
  initMsal,
}))

describe('getDelegatedApiAuth', () => {
  beforeEach(() => {
    resetDelegatedApiAuthCache()
    window.sessionStorage.clear()
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ client_id: 'client-id', tenant_id: 'tenant-id' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    ) as typeof fetch
    initMsal.mockReset()
    initMsal.mockResolvedValue(undefined)
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

    expect(initMsal).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' })
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

    expect(initMsal).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' })
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

    expect(initMsal).toHaveBeenCalledWith({ clientId: 'client-id', tenantId: 'tenant-id' })
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

  it('bootstraps redirect handling only once for repeated calls', async () => {
    getWorkIqFoundryAuth.mockResolvedValue({ token: 'foundry-token', status: 'ok' })
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    await getDelegatedApiAuth({ interactive: false })
    await getDelegatedApiAuth({ interactive: false })

    expect(initMsal).toHaveBeenCalledTimes(1)
  })

  it('retries loading MSAL config after an initial bootstrap fetch failure', async () => {
    global.fetch = vi.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValue(new Response(JSON.stringify({ client_id: 'client-id', tenant_id: 'tenant-id' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch
    getWorkIqFoundryAuth.mockResolvedValue({ token: 'foundry-token', status: 'ok' })
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    const firstResult = await getDelegatedApiAuth({ interactive: false })
    const secondResult = await getDelegatedApiAuth({ interactive: false })

    expect(firstResult).toEqual({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
    expect(secondResult).toEqual({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
    expect(global.fetch).toHaveBeenCalledTimes(2)
  })

  it('retries bootstrap on a later call when startup config loading initially returns unavailable', async () => {
    global.fetch = vi.fn()
      .mockRejectedValueOnce(new Error('network'))
      .mockResolvedValueOnce(new Response(JSON.stringify({ client_id: 'client-id', tenant_id: 'tenant-id' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })) as typeof fetch

    await bootstrapDelegatedApiAuth()
    await bootstrapDelegatedApiAuth()

    expect(global.fetch).toHaveBeenCalledTimes(2)
    expect(initMsal).toHaveBeenCalledTimes(1)
  })

  it('retries bootstrap after an initial MSAL initialization failure', async () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})
    initMsal
      .mockRejectedValueOnce(new Error('redirect bridge failed'))
      .mockResolvedValue(undefined)
    getWorkIqFoundryAuth.mockResolvedValue({ token: 'foundry-token', status: 'ok' })
    getWorkIqGraphAuth.mockResolvedValue({ token: 'graph-token', status: 'ok' })

    const firstResult = await getDelegatedApiAuth({ interactive: false })
    const secondResult = await getDelegatedApiAuth({ interactive: false })

    expect(firstResult).toEqual({
      headers: {
        Authorization: 'Bearer foundry-token',
        'X-Work-IQ-Graph-Authorization': 'Bearer graph-token',
      },
      status: 'ok',
    })
    expect(secondResult).toEqual(firstResult)
    expect(initMsal).toHaveBeenCalledTimes(2)
    expect(warnSpy).toHaveBeenCalledWith('Delegated auth bootstrap failed:', expect.any(Error))
  })

  it('reuses the cached MSAL config from sessionStorage without refetching voice-config', async () => {
    window.sessionStorage.setItem(MSAL_CONFIG_CACHE_KEY, JSON.stringify({
      clientId: 'cached-client-id',
      tenantId: 'cached-tenant-id',
    }))

    await bootstrapDelegatedApiAuth()

    expect(global.fetch).not.toHaveBeenCalled()
    expect(initMsal).toHaveBeenCalledWith({
      clientId: 'cached-client-id',
      tenantId: 'cached-tenant-id',
    })
  })
})
