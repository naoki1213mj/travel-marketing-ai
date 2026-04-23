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

const WORK_IQ_SCOPE_SET = [
  'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
  'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Calendar.All',
  'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
  'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.OneDriveSharepoint.All',
  'api://ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.CopilotMCP.All',
]

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
    window.sessionStorage.clear()
    window.location.hash = ''
    window.location.search = ''
  })

  it('uses the dedicated redirect page and always gives MSAL a chance to finish redirect handling', async () => {
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
    expect(handleRedirectPromiseMock).toHaveBeenCalledWith({ navigateToLoginRequestUrl: false })
  })

  it('handles a redirect response on pages that actually contain an auth callback hash', async () => {
    window.location.hash = '#code=abc&state=xyz'

    const { initMsal } = await import('./msal-auth')

    await initMsal({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(handleRedirectPromiseMock).toHaveBeenCalledWith({ navigateToLoginRequestUrl: false })
  })

  it('uses a cached redirect result even after the dedicated redirect page has already returned to / without a hash', async () => {
    const redirectAccount = { username: 'user@example.com' }
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      expiresOn: new Date(Date.now() + 60_000),
      scopes: WORK_IQ_SCOPE_SET,
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'redirect-token', status: 'ok' })
  })

  it('sets the redirect response account as the active account before silent token acquisition', async () => {
    const redirectAccount = { username: 'user@example.com' }
    window.location.hash = '#code=abc&state=xyz'
    handleRedirectPromiseMock.mockResolvedValue({ account: redirectAccount })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(setActiveAccountMock).toHaveBeenCalledWith(redirectAccount)
    expect(acquireTokenSilentMock).toHaveBeenCalledWith({
      scopes: WORK_IQ_SCOPE_SET,
      account: redirectAccount,
    })
    expect(result).toEqual({ token: 'token', status: 'ok' })
  })

  it('reuses the redirect response access token for the immediate post-login Work IQ request', async () => {
    const redirectAccount = { username: 'user@example.com' }
    window.location.hash = '#code=abc&state=xyz'
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      expiresOn: new Date(Date.now() + 60_000),
      scopes: WORK_IQ_SCOPE_SET,
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'redirect-token', status: 'ok' })
    expect(window.sessionStorage.getItem('workIqMsalRedirectBridge')).toBeNull()
  })

  it('writes a bridge token when the main app finishes redirect handling', async () => {
    const redirectAccount = { username: 'user@example.com' }
    window.location.hash = '#code=abc&state=xyz'
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      expiresOn: new Date(Date.now() + 60_000),
      scopes: WORK_IQ_SCOPE_SET,
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { initMsal } = await import('./msal-auth')

    await initMsal({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(window.sessionStorage.getItem('workIqMsalRedirectBridge')).toContain('redirect-token')
  })

  it('falls back to silent token acquisition when the redirect response scopes only partially cover Work IQ', async () => {
    const redirectAccount = { username: 'user@example.com' }
    window.location.hash = '#code=abc&state=xyz'
    acquireTokenSilentMock.mockResolvedValue({ accessToken: 'silent-token' })
    handleRedirectPromiseMock.mockResolvedValue({
      account: redirectAccount,
      accessToken: 'redirect-token',
      scopes: [
        'https://graph.microsoft.com/Sites.Read.All',
        'https://graph.microsoft.com/Chat.Read',
      ],
    })
    setActiveAccountMock.mockImplementation((account) => {
      getActiveAccountMock.mockReturnValue(account)
    })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).toHaveBeenCalledWith({
      scopes: WORK_IQ_SCOPE_SET,
      account: redirectAccount,
    })
    expect(result).toEqual({ token: 'silent-token', status: 'ok' })
  })

  it('returns redirecting after starting an interactive login redirect', async () => {
    vi.useFakeTimers()
    acquireTokenRedirectMock.mockImplementation(() => new Promise<void>(() => {}))

    try {
      const { getWorkIqFoundryAuth } = await import('./msal-auth')

      const resultPromise = getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' }, true)
      await vi.advanceTimersByTimeAsync(200)

      expect(acquireTokenRedirectMock).toHaveBeenCalledWith({
        scopes: WORK_IQ_SCOPE_SET,
      })
      await expect(resultPromise).resolves.toEqual({ token: null, status: 'redirecting' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('returns unavailable when interactive redirect setup fails immediately', async () => {
    acquireTokenRedirectMock.mockRejectedValue(new Error('interaction_in_progress'))

    const { getWorkIqFoundryAuth } = await import('./msal-auth')
    const { readMsalRedirectFailureSentinel } = await import('./msal-redirect-sentinel')

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' }, true)

    expect(result).toEqual({ token: null, status: 'unavailable' })
    expect(readMsalRedirectFailureSentinel()).toEqual(expect.objectContaining({
      stage: 'main_app',
      message: 'interaction_in_progress',
    }))
  })

  it('requests Agent 365 delegated scopes for Work IQ preflight', async () => {
    const account = { username: 'user@example.com' }
    getAllAccountsMock.mockReturnValue([account])
    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).toHaveBeenCalledWith(expect.objectContaining({
      scopes: WORK_IQ_SCOPE_SET,
    }))
  })

  it('retries initialization after a failed redirect handling attempt', async () => {
    window.location.hash = '#error=access_denied&state=xyz'
    handleRedirectPromiseMock
      .mockRejectedValueOnce(new Error('bridge failed'))
      .mockResolvedValueOnce(null)
    getAllAccountsMock.mockReturnValue([{ username: 'user@example.com' }])

    const { getWorkIqFoundryAuth } = await import('./msal-auth')

    await expect(getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })).rejects.toThrow('bridge failed')

    const { readMsalRedirectFailureSentinel } = await import('./msal-redirect-sentinel')

    expect(readMsalRedirectFailureSentinel()).toEqual(expect.objectContaining({
      stage: 'main_app',
      message: 'bridge failed',
    }))

    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(publicClientApplicationMock).toHaveBeenCalledTimes(2)
    expect(initializeMock).toHaveBeenCalledTimes(2)
    expect(result).toEqual({ token: 'token', status: 'ok' })
  })

  // --- msal-redirect-bridge: post-login resume path ---

  it('uses the cached redirect bridge token without calling acquireTokenSilent', async () => {
    // redirect 処理後に保持された bridge token をシミュレート
    const bridgeScopes = WORK_IQ_SCOPE_SET
    window.sessionStorage.setItem('workIqMsalRedirectBridge', JSON.stringify({
      accessToken: 'bridge-access-token',
      scopes: bridgeScopes,
      expiresAt: Date.now() + 3_600_000,
    }))
    // MSAL のアカウントキャッシュにもアカウントを設定（bridge page が setActiveAccount 済みを模擬）
    const bridgeAccount = { username: 'user@example.com' }
    getAllAccountsMock.mockReturnValue([bridgeAccount])

    const { getWorkIqFoundryAuth } = await import('./msal-auth')
    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    // ブリッジトークンを直接使うため silent acquisition は不要
    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'bridge-access-token', status: 'ok' })
    // 消費後はブリッジエントリが消えていること
    expect(window.sessionStorage.getItem('workIqMsalRedirectBridge')).toBeNull()
  })

  it('ignores an expired bridge result and falls back to silent token acquisition', async () => {
    const bridgeAccount = { username: 'user@example.com' }
    getAllAccountsMock.mockReturnValue([bridgeAccount])
    // 期限切れのブリッジ結果
    window.sessionStorage.setItem('workIqMsalRedirectBridge', JSON.stringify({
      accessToken: 'old-bridge-token',
      scopes: ['https://graph.microsoft.com/Sites.Read.All'],
      expiresAt: Date.now() - 1_000,
    }))
    acquireTokenSilentMock.mockResolvedValue({ accessToken: 'silent-token' })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')
    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(acquireTokenSilentMock).toHaveBeenCalled()
    expect(result).toEqual({ token: 'silent-token', status: 'ok' })
  })

  it('uses bridge token as safety fallback when MSAL account cache is empty after redirect', async () => {
    // accounts が空（MSAL のアカウントキャッシュが引き継がれなかったエッジケース）
    getAllAccountsMock.mockReturnValue([])
    const bridgeScopes = WORK_IQ_SCOPE_SET
    window.sessionStorage.setItem('workIqMsalRedirectBridge', JSON.stringify({
      accessToken: 'bridge-fallback-token',
      scopes: bridgeScopes,
      expiresAt: Date.now() + 3_600_000,
    }))

    const { getWorkIqFoundryAuth } = await import('./msal-auth')
    // interactive=false（リダイレクト後の silent リトライ）
    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' }, false)

    expect(acquireTokenSilentMock).not.toHaveBeenCalled()
    expect(result).toEqual({ token: 'bridge-fallback-token', status: 'ok' })
  })

  it('clears the bridge entry and falls back to silent when scopes do not match the request', async () => {
    getAllAccountsMock.mockReturnValue([{ username: 'user@example.com' }])
    // ブリッジ結果は一部の Graph スコープしか持たないため、要求セットを満たせない
    window.sessionStorage.setItem('workIqMsalRedirectBridge', JSON.stringify({
      accessToken: 'partial-bridge-token',
      scopes: ['https://graph.microsoft.com/Sites.Read.All'],
      expiresAt: Date.now() + 3_600_000,
    }))
    acquireTokenSilentMock.mockResolvedValue({ accessToken: 'silent-foundry-token' })

    const { getWorkIqFoundryAuth } = await import('./msal-auth')
    const result = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    // スコープ不一致 → silent acquisition にフォールバック
    expect(acquireTokenSilentMock).toHaveBeenCalled()
    expect(result).toEqual({ token: 'silent-foundry-token', status: 'ok' })
  })

  it('does not reuse an expired in-memory bridge token after a prior scope mismatch', async () => {
    getAllAccountsMock.mockReturnValue([{ username: 'user@example.com' }])
    const realDateNow = Date.now
    const bridgeExpiry = realDateNow() + 5_000
    window.sessionStorage.setItem('workIqMsalRedirectBridge', JSON.stringify({
      accessToken: 'partial-bridge-token',
      scopes: WORK_IQ_SCOPE_SET,
      expiresAt: bridgeExpiry,
    }))
    acquireTokenSilentMock
      .mockResolvedValueOnce({ accessToken: 'voice-live-token' })
      .mockResolvedValueOnce({ accessToken: 'refreshed-foundry-token' })

    const { getVoiceLiveToken, getWorkIqFoundryAuth } = await import('./msal-auth')

    const voiceResult = await getVoiceLiveToken({ clientId: 'client-id', tenantId: 'tenant-id' })
    expect(voiceResult).toEqual('voice-live-token')

    vi.spyOn(Date, 'now').mockReturnValue(bridgeExpiry + 1)

    const foundryResult = await getWorkIqFoundryAuth({ clientId: 'client-id', tenantId: 'tenant-id' })

    expect(foundryResult).toEqual({ token: 'refreshed-foundry-token', status: 'ok' })
    expect(acquireTokenSilentMock).toHaveBeenCalledTimes(2)
    vi.restoreAllMocks()
  })
})
