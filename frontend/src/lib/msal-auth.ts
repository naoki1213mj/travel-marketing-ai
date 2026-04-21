/**
 * MSAL.js 認証。Voice Live 用のユーザー委任トークンを取得する。
 *
 * Voice Live WebSocket はユーザー委任 AAD トークンが必要（MI トークンは 1006 で拒否される）。
 * ブラウザ上で MSAL.js を使い、Entra ID でユーザー認証してトークンを取得する。
 *
 * Edge の COOP (Cross-Origin-Opener-Policy) でポップアップがブロックされるため、
 * redirect 方式を使用する。
 */

import { PublicClientApplication, type SilentRequest, InteractionRequiredAuthError } from '@azure/msal-browser'

let msalInstance: PublicClientApplication | null = null
let initPromise: Promise<void> | null = null

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
const WORK_IQ_FOUNDRY_SCOPES = [
  `${AGENT_365_TOOLS_APP_ID}/McpServers.Mail.All`,
  `${AGENT_365_TOOLS_APP_ID}/McpServers.Calendar.All`,
  `${AGENT_365_TOOLS_APP_ID}/McpServers.Teams.All`,
  `${AGENT_365_TOOLS_APP_ID}/McpServers.OneDriveSharepoint.All`,
]

export async function initMsal(config: MsalConfig): Promise<void> {
  if (msalInstance) return
  if (initPromise) { await initPromise; return }

  initPromise = (async () => {
    msalInstance = new PublicClientApplication({
      auth: {
        clientId: config.clientId,
        authority: `https://login.microsoftonline.com/${config.tenantId}`,
        redirectUri: window.location.origin,
        // redirectUri と元画面が同一なので、戻り先の再ナビゲーションは抑止する。
        navigateToLoginRequestUrl: false,
      },
      cache: {
        cacheLocation: 'sessionStorage',
      },
    })

    await msalInstance.initialize()
    // redirect からの戻りを処理
    const redirectResponse = await msalInstance.handleRedirectPromise()
    if (redirectResponse?.account) {
      msalInstance.setActiveAccount(redirectResponse.account)
      return
    }

    if (!msalInstance.getActiveAccount()) {
      const accounts = msalInstance.getAllAccounts()
      if (accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0])
      }
    }
  })()

  await initPromise
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
    try {
      const request: SilentRequest = {
        scopes,
        account: accounts[0],
      }
      const response = await msalInstance.acquireTokenSilent(request)
      return { token: response.accessToken, status: 'ok' }
    } catch (err) {
      if (interactive && err instanceof InteractionRequiredAuthError) {
        // サイレント失敗 → redirect で再認証
        await msalInstance.acquireTokenRedirect({ scopes })
        return { token: null, status: 'redirecting' } // redirect するため、ここには戻らない
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
    await msalInstance.acquireTokenRedirect({ scopes })
    // redirect するため、ここには戻らない
    return { token: null, status: 'redirecting' }
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
