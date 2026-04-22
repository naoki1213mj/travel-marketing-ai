import { beforeEach, describe, expect, it, vi } from 'vitest'

const {
  initializeMock,
  handleRedirectPromiseMock,
  setActiveAccountMock,
  getActiveAccountMock,
  getAllAccountsMock,
  acquireTokenSilentMock,
  acquireTokenRedirectMock,
  publicClientApplicationMock,
} = vi.hoisted(() => ({
  initializeMock: vi.fn(async () => {}),
  handleRedirectPromiseMock: vi.fn(async () => null),
  setActiveAccountMock: vi.fn(),
  getActiveAccountMock: vi.fn(() => null),
  getAllAccountsMock: vi.fn(() => []),
  acquireTokenSilentMock: vi.fn(async () => ({ accessToken: 'token' })),
  acquireTokenRedirectMock: vi.fn(async () => {}),
  publicClientApplicationMock: vi.fn(function PublicClientApplication() {
    return {
      initialize: initializeMock,
      handleRedirectPromise: handleRedirectPromiseMock,
      setActiveAccount: setActiveAccountMock,
      getActiveAccount: getActiveAccountMock,
      getAllAccounts: getAllAccountsMock,
      acquireTokenSilent: acquireTokenSilentMock,
      acquireTokenRedirect: acquireTokenRedirectMock,
    }
  }),
}))

vi.mock('@azure/msal-browser', () => ({
  PublicClientApplication: publicClientApplicationMock,
  InteractionRequiredAuthError: class InteractionRequiredAuthError extends Error {
    errorCode = 'interaction_required'
    subError = ''
  },
}))

describe('msal-auth', () => {
  beforeEach(() => {
    vi.resetModules()
    publicClientApplicationMock.mockClear()
    initializeMock.mockClear()
    handleRedirectPromiseMock.mockReset()
    handleRedirectPromiseMock.mockResolvedValue(null)
    setActiveAccountMock.mockClear()
    getActiveAccountMock.mockReset()
    getActiveAccountMock.mockReturnValue(null)
    getAllAccountsMock.mockReset()
    getAllAccountsMock.mockReturnValue([])
    acquireTokenSilentMock.mockReset()
    acquireTokenSilentMock.mockResolvedValue({ accessToken: 'token' })
    acquireTokenRedirectMock.mockClear()
  })

  it('uses the dedicated redirect bridge page for MSAL auth flows', async () => {
    const { initMsal } = await import('./msal-auth')

    await initMsal({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(publicClientApplicationMock).toHaveBeenCalledWith({
      auth: {
        clientId: 'client-id',
        authority: 'https://login.microsoftonline.com/tenant-id',
        redirectUri: `${window.location.origin}/auth-redirect.html`,
      },
      cache: {
        cacheLocation: 'sessionStorage',
      },
    })
    // handleRedirectPromise は navigateToLoginRequestUrl:false で呼ばれる必要がある
    // （bridge 側で token 交換済みの想定。main app が勝手に再 navigate しないため）。
    expect(handleRedirectPromiseMock).toHaveBeenCalledWith({ navigateToLoginRequestUrl: false })
  })

  it('sets the redirect response account as the active account before silent token acquisition', async () => {
    const redirectAccount = { username: 'user@example.com' }
    handleRedirectPromiseMock.mockResolvedValue({ account: redirectAccount })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(setActiveAccountMock).toHaveBeenCalledWith(redirectAccount)
    expect(acquireTokenSilentMock).toHaveBeenCalledWith({
      scopes: [
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Calendar.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.OneDriveSharepoint.All',
      ],
      account: redirectAccount,
    })
    expect(result).toEqual({ token: 'token', status: 'ok' })
  })

  it('reuses the redirect response access token for the immediate post-login Work IQ request', async () => {
    const redirectAccount = { username: 'user@example.com' }
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      scopes: [
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Calendar.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.OneDriveSharepoint.All',
      ],
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'redirect-token', status: 'ok' })
  })

  it('reuses the redirect response token when returned scopes partially overlap the requested Work IQ scopes', async () => {
    const redirectAccount = { username: 'user@example.com' }
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      scopes: [
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
      ],
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'redirect-token', status: 'ok' })
  })

  it('returns redirecting immediately for interactive login redirects', async () => {
    acquireTokenRedirectMock.mockImplementation(() => new Promise<void>(() => {}))

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await Promise.race([
      getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' }, true),
      new Promise<DelegatedTokenResult>((resolve) => {
        setTimeout(() => resolve({ token: null, status: 'unavailable' }), 25)
      }),
    ])

    expect(acquireTokenRedirectMock).toHaveBeenCalledWith({
      scopes: [
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Calendar.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
        'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.OneDriveSharepoint.All',
      ],
    })
    expect(result).toEqual({ token: null, status: 'redirecting' })
  })

  it('requests Agent 365 Tools scopes via the documented api:// app ID URI', async () => {
    const account = { username: 'user@example.com' }
    getAllAccountsMock.mockReturnValue([account])
    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).toHaveBeenCalledWith(expect.objectContaining({
      scopes: expect.arrayContaining([
        expect.stringMatching(/^api:\/\/ea9ffc3e-8a23-4a7d-836d-234d7c7565c1\/McpServers\./),
      ]),
    }))
  })

  it('retries initialization after a failed redirect handling attempt', async () => {
    handleRedirectPromiseMock
      .mockRejectedValueOnce(new Error('bridge failed'))
      .mockResolvedValueOnce(null)
    getAllAccountsMock.mockReturnValue([{ username: 'user@example.com' }])

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    await expect(getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })).rejects.toThrow('bridge failed')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(publicClientApplicationMock).toHaveBeenCalledTimes(2)
    expect(initializeMock).toHaveBeenCalledTimes(2)
    expect(result).toEqual({ token: 'token', status: 'ok' })
  })
})
