/**
 * MSAL.js 認証。Voice Live 用のユーザー委任トークンを取得する。
 *
 * Voice Live WebSocket はユーザー委任 AAD トークンが必要（MI トークンは 1006 で拒否される）。
 * ブラウザ上で MSAL.js を使い、Entra ID でユーザー認証してトークンを取得する。
 *
 * Edge の COOP (Cross-Origin-Opener-Policy) でポップアップがブロックされるため、
 * redirect 方式を使用する。
 */

import {
  PublicClientApplication,
  type AuthenticationResult,
  type SilentRequest,
  InteractionRequiredAuthError,
} from '@azure/msal-browser'

let msalInstance: PublicClientApplication | null = null
let initPromise: Promise<void> | null = null
let msalInitialized = false
let pendingRedirectResult: AuthenticationResult | null = null

export interface MsalConfig {
  clientId: string
  tenantId: string
}

export type DelegatedAuthStatus = 'ok' | 'auth_required' | 'consent_required' | 'redirecting' | 'unavailable'

export interface DelegatedTokenResult {
  token: string | null
  status: DelegatedAuthStatus
}

const VOICE_LIVE_SCOPES = ['https://cognitiveservices.azure.com/user_impersonation']
const WORK_IQ_GRAPH_SCOPES = [
  'https://graph.microsoft.com/Sites.Read.All',
  'https://graph.microsoft.com/Mail.Read',
  'https://graph.microsoft.com/People.Read.All',
  'https://graph.microsoft.com/OnlineMeetingTranscript.Read.All',
  'https://graph.microsoft.com/Chat.Read',
  'https://graph.microsoft.com/ChannelMessage.Read.All',
  'https://graph.microsoft.com/ExternalItem.Read.All',
]
const AGENT_365_TOOLS_APP_ID = 'ea9ffc3e-8a23-4a7d-836d-234d7c7565c1'
const AGENT_365_TOOLS_APP_ID_URI = `api://${AGENT_365_TOOLS_APP_ID}`
function buildAgent365Scope(scopeName: string): string {
  return `${AGENT_365_TOOLS_APP_ID_URI}/${scopeName}`
}
const WORK_IQ_FOUNDRY_SCOPES = [
  buildAgent365Scope('McpServers.Mail.All'),
  buildAgent365Scope('McpServers.Calendar.All'),
  buildAgent365Scope('McpServers.Teams.All'),
  buildAgent365Scope('McpServers.OneDriveSharepoint.All'),
]
const MSAL_REDIRECT_PATH = '/auth-redirect.html'

function normalizeScopes(scopes: string[]): string[] {
  return scopes
    .map(scope => scope.trim().toLowerCase())
    .filter(scope => scope.length > 0)
}

function consumePendingRedirectToken(scopes: string[]): DelegatedTokenResult | null {
  if (!pendingRedirectResult) return null

  const redirectToken = typeof pendingRedirectResult.accessToken === 'string'
    ? pendingRedirectResult.accessToken.trim()
    : ''
  const redirectScopes = normalizeScopes(pendingRedirectResult.scopes ?? [])
  const requestedScopes = normalizeScopes(scopes)

  if (!redirectToken || redirectScopes.length === 0 || requestedScopes.length === 0) {
    pendingRedirectResult = null
    return null
  }

  const redirectScopeSet = new Set(redirectScopes)
  const hasScopeOverlap = requestedScopes.some(scope => redirectScopeSet.has(scope))
  if (!hasScopeOverlap) {
    return null
  }

  pendingRedirectResult = null
  return { token: redirectToken, status: 'ok' }
}

export async function initMsal(config: MsalConfig): Promise<void> {
  if (msalInitialized && msalInstance) return
  if (initPromise) { await initPromise; return }

  const nextInstance = new PublicClientApplication({
    auth: {
      clientId: config.clientId,
      authority: `https://login.microsoftonline.com/${config.tenantId}`,
      redirectUri: new URL(MSAL_REDIRECT_PATH, window.location.origin).toString(),
    },
    cache: {
      cacheLocation: 'sessionStorage',
    },
  })

  initPromise = (async () => {
    await nextInstance.initialize()
    // bridge (/auth-redirect.html) で token 交換済みなら main app に hash は残らず
    // null が返る。万一 main app が hash 付きで起動した場合も、MSAL が勝手に
    // request.origin へ再 navigate しないよう navigateToLoginRequestUrl:false で固定する。
    const redirectResponse = await nextInstance.handleRedirectPromise({
      navigateToLoginRequestUrl: false,
    })
    if (redirectResponse?.account) {
      pendingRedirectResult = redirectResponse
      nextInstance.setActiveAccount(redirectResponse.account)
      msalInstance = nextInstance
      msalInitialized = true
      return
    }

    if (!nextInstance.getActiveAccount()) {
      const accounts = nextInstance.getAllAccounts()
      if (accounts.length > 0) {
        nextInstance.setActiveAccount(accounts[0])
      }
    }

    msalInstance = nextInstance
    msalInitialized = true
  })()

  try {
    await initPromise
  } catch (error) {
    msalInstance = null
    msalInitialized = false
    throw error
  } finally {
    initPromise = null
  }
}

function beginRedirectAuth(
  instance: PublicClientApplication,
  scopes: string[],
): DelegatedTokenResult {
  void instance.acquireTokenRedirect({ scopes }).catch((err: unknown) => {
    console.warn('MSAL redirect token acquisition failed:', err)
  })
  return { token: null, status: 'redirecting' }
}

async function acquireDelegatedToken(
  config: MsalConfig,
  scopes: string[],
  interactive: boolean,
): Promise<DelegatedTokenResult> {
  await initMsal(config)
  if (!msalInstance) return { token: null, status: 'unavailable' }

  const activeAccount = msalInstance.getActiveAccount()
  const accounts = activeAccount ? [activeAccount] : msalInstance.getAllAccounts()

  if (accounts.length > 0) {
    const redirectResult = consumePendingRedirectToken(scopes)
    if (redirectResult) {
      return redirectResult
    }
    try {
      const request: SilentRequest = {
        scopes,
        account: accounts[0],
      }
      const response = await msalInstance.acquireTokenSilent(request)
      return { token: response.accessToken, status: 'ok' }
    } catch (err) {
      if (interactive && err instanceof InteractionRequiredAuthError) {
        // redirect API の Promise 完了には依存せず、直ちに UI を redirecting 扱いにする。
        return beginRedirectAuth(msalInstance, scopes)
      }
      if (err instanceof InteractionRequiredAuthError) {
        const errorCode = String(err.errorCode || '').trim().toLowerCase()
        const subError = String(err.subError || '').trim().toLowerCase()
        return {
          token: null,
          status: errorCode === 'consent_required' || subError === 'consent_required'
            ? 'consent_required'
            : 'auth_required',
        }
      }
      console.warn('MSAL silent token failed:', err)
      return { token: null, status: 'unavailable' }
    }
  }

  if (!interactive) {
    return { token: null, status: 'auth_required' }
  }

  // 未ログイン → redirect でログイン
  try {
    return beginRedirectAuth(msalInstance, scopes)
  } catch (err) {
    console.warn('MSAL token acquisition failed:', err)
    return { token: null, status: 'unavailable' }
  }
}

export async function getVoiceLiveToken(config: MsalConfig): Promise<string | null> {
  return (await acquireDelegatedToken(config, VOICE_LIVE_SCOPES, true)).token
}

export async function getWorkIqGraphToken(config: MsalConfig, interactive = false): Promise<string | null> {
  return (await acquireDelegatedToken(config, WORK_IQ_GRAPH_SCOPES, interactive)).token
}

export async function getWorkIqGraphAuth(config: MsalConfig, interactive = false): Promise<DelegatedTokenResult> {
  return acquireDelegatedToken(config, WORK_IQ_GRAPH_SCOPES, interactive)
}

export async function getWorkIqFoundryToken(config: MsalConfig, interactive = false): Promise<string | null> {
  return (await acquireDelegatedToken(config, WORK_IQ_FOUNDRY_SCOPES, interactive)).token
}

export async function getWorkIqFoundryAuth(config: MsalConfig, interactive = false): Promise<DelegatedTokenResult> {
  return acquireDelegatedToken(config, WORK_IQ_FOUNDRY_SCOPES, interactive)
}
