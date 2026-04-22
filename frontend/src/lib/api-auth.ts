import { getWorkIqFoundryAuth, getWorkIqGraphAuth, initMsal, type DelegatedAuthStatus } from './msal-auth'
import {
  normalizeMsalConfig,
  readCachedMsalConfig,
  writeCachedMsalConfig,
  type MsalConfig,
} from './msal-config-cache'

let cachedMsalConfig: MsalConfig | null = null
let cachedMsalConfigPromise: Promise<MsalConfig | null> | null = null
let delegatedAuthBootstrapped = false
let delegatedAuthBootstrapPromise: Promise<void> | null = null

async function getMsalConfig(): Promise<MsalConfig | null> {
  if (cachedMsalConfig) {
    return cachedMsalConfig
  }

  const cachedStoredConfig = readCachedMsalConfig()
  if (cachedStoredConfig) {
    cachedMsalConfig = cachedStoredConfig
    return cachedStoredConfig
  }

  if (!cachedMsalConfigPromise) {
    cachedMsalConfigPromise = (async () => {
      try {
        const response = await fetch('/api/voice-config')
        if (!response.ok) return null
        const config = normalizeMsalConfig(await response.json())
        if (config) {
          cachedMsalConfig = config
          writeCachedMsalConfig(config)
        }
        return config
      } catch {
        return null
      } finally {
        cachedMsalConfigPromise = null
      }
    })()
  }
  return cachedMsalConfigPromise
}

export async function bootstrapDelegatedApiAuth(): Promise<void> {
  if (delegatedAuthBootstrapped) {
    return
  }
  if (!delegatedAuthBootstrapPromise) {
    delegatedAuthBootstrapPromise = (async () => {
      const config = await getMsalConfig()
      if (!config) return
      await initMsal(config)
      delegatedAuthBootstrapped = true
    })()
  }

  try {
    await delegatedAuthBootstrapPromise
  } finally {
    delegatedAuthBootstrapPromise = null
  }
}

export interface DelegatedApiAuthResult {
  headers: Record<string, string>
  status: DelegatedAuthStatus
}

export async function getDelegatedApiAuth(
  options?: { interactive?: boolean; workIqRuntime?: 'graph_prefetch' | 'foundry_tool' },
): Promise<DelegatedApiAuthResult> {
  try {
    await bootstrapDelegatedApiAuth()
  } catch (error) {
    console.warn('Delegated auth bootstrap failed:', error)
  }
  const config = await getMsalConfig()
  if (!config) {
    return { headers: {}, status: 'unavailable' }
  }

  const runtime = options?.workIqRuntime === 'graph_prefetch' ? 'graph_prefetch' : 'foundry_tool'
  if (runtime === 'graph_prefetch') {
    const result = await getWorkIqGraphAuth(config, options?.interactive === true)
    return {
      headers: result.token ? { Authorization: `Bearer ${result.token}` } : {},
      status: result.status,
    }
  }

  const result = await getWorkIqFoundryAuth(config, options?.interactive === true)
  const headers: Record<string, string> = result.token ? { Authorization: `Bearer ${result.token}` } : {}
  if (result.status === 'ok') {
    const graphResult = await getWorkIqGraphAuth(config, false)
    if (graphResult.token) {
      headers['X-Work-IQ-Graph-Authorization'] = `Bearer ${graphResult.token}`
    }
  }

  return {
    headers,
    status: result.status,
  }
}

export async function getDelegatedApiHeaders(options?: { interactive?: boolean }): Promise<Record<string, string>> {
  return (await getDelegatedApiAuth(options)).headers
}

export function resetDelegatedApiAuthCache(): void {
  cachedMsalConfig = null
  cachedMsalConfigPromise = null
  delegatedAuthBootstrapped = false
  delegatedAuthBootstrapPromise = null
}
