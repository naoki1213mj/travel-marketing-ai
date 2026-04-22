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
import { clearMsalRedirectFailureSentinel, recordMsalRedirectFailureSentinel } from './msal-redirect-sentinel'
import { readAndClearRedirectBridgeResult } from './msal-redirect-bridge'
import type { MsalConfig } from './msal-config-cache'

let msalInstance: PublicClientApplication | null = null
let initPromise: Promise<void> | null = null
let msalInitialized = false
let pendingRedirectResult: AuthenticationResult | null = null
let pendingRedirectExpiresAt: number = 0

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

function locationHasMsalAuthResponse(): boolean {
  const currentLocation = `${window.location.hash}${window.location.search}`.toLowerCase()
  return /(?:^|[?#&])(code|error)=/.test(currentLocation)
}

function normalizeScopes(scopes: string[]): string[] {
  return scopes
    .map(scope => scope.trim().toLowerCase())
    .filter(scope => scope.length > 0)
}

function hasRequiredScopeCoverage(requestedScopes: string[], grantedScopes: string[]): boolean {
  if (requestedScopes.length === 0 || grantedScopes.length === 0) {
    return false
  }

  const grantedScopeSet = new Set(grantedScopes)
  return requestedScopes.every(scope => grantedScopeSet.has(scope))
}

function consumePendingRedirectToken(scopes: string[]): DelegatedTokenResult | null {
  if (!pendingRedirectResult) return null

  if (pendingRedirectExpiresAt > 0 && pendingRedirectExpiresAt <= Date.now()) {
    pendingRedirectResult = null
    pendingRedirectExpiresAt = 0
    return null
  }

  const redirectToken = typeof pendingRedirectResult.accessToken === 'string'
    ? pendingRedirectResult.accessToken.trim()
    : ''
  const redirectScopes = normalizeScopes(pendingRedirectResult.scopes ?? [])
  const requestedScopes = normalizeScopes(scopes)

  if (!redirectToken || redirectScopes.length === 0 || requestedScopes.length === 0) {
    pendingRedirectResult = null
    pendingRedirectExpiresAt = 0
    return null
  }

  if (!hasRequiredScopeCoverage(requestedScopes, redirectScopes)) {
    return null
  }

  pendingRedirectResult = null
  pendingRedirectExpiresAt = 0
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
    let redirectResponse: AuthenticationResult | null = null
    if (locationHasMsalAuthResponse()) {
      try {
        redirectResponse = await nextInstance.handleRedirectPromise({
          navigateToLoginRequestUrl: false,
        })
      } catch (error) {
        recordMsalRedirectFailureSentinel('main_app', error)
        throw error
      }
    }
    if (redirectResponse?.account) {
      clearMsalRedirectFailureSentinel()
      pendingRedirectResult = redirectResponse
      pendingRedirectExpiresAt = redirectResponse.expiresOn?.getTime() ?? 0
      nextInstance.setActiveAccount(redirectResponse.account)
      msalInstance = nextInstance
      msalInitialized = true
      return
    }

    // auth-redirect.html のブリッジページがトークンを書き込んでいれば読み取る。
    // acquireTokenSilent が新規 PCA インスタンスで InteractionRequiredAuthError を
    // 投げるエッジケースに備え、直接取得したトークンを pendingRedirectResult に設定する。
    if (!pendingRedirectResult) {
      const bridgeResult = readAndClearRedirectBridgeResult()
      if (bridgeResult) {
        clearMsalRedirectFailureSentinel()
        pendingRedirectResult = {
          accessToken: bridgeResult.accessToken,
          scopes: bridgeResult.scopes,
        } as AuthenticationResult
        pendingRedirectExpiresAt = bridgeResult.expiresAt
      }
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
    pendingRedirectResult = null
    pendingRedirectExpiresAt = 0
    throw error
  } finally {
    initPromise = null
  }
}

async function beginRedirectAuth(
  instance: PublicClientApplication,
  scopes: string[],
): Promise<DelegatedTokenResult> {
  clearMsalRedirectFailureSentinel()
  let failureError: unknown = null
  const redirectPromise = instance.acquireTokenRedirect({ scopes }).catch((err: unknown) => {
    failureError = err
    console.warn('MSAL redirect token acquisition failed:', err)
    recordMsalRedirectFailureSentinel('main_app', err)
  })
  // 即時拒否（interaction_in_progress、テナント未登録など）を検出するため短時間待機する。
  // acquireTokenRedirect が正常にブラウザ遷移した場合、ページが離れるため Promise は永遠に解決しない。
  await Promise.race([redirectPromise, new Promise<void>(resolve => setTimeout(resolve, 200))])
  if (failureError !== null) {
    return { token: null, status: 'unavailable' }
  }
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
    // accounts が空の場合でもブリッジトークンが残っていれば使う（アカウントキャッシュ
    // の取得に失敗したエッジケース対策）。
    const bridgeFallback = consumePendingRedirectToken(scopes)
    if (bridgeFallback) {
      return bridgeFallback
    }
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
