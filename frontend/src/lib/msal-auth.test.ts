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
        'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Mail.All',
        'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Calendar.All',
        'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.Teams.All',
        'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1/McpServers.OneDriveSharepoint.All',
      ],
      account: redirectAccount,
    })
    expect(result).toEqual({ token: 'token', status: 'ok' })
  })
})
