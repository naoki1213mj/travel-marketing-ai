import { getWorkIqGraphToken, type MsalConfig } from './msal-auth'

let cachedMsalConfigPromise: Promise<MsalConfig | null> | null = null

function normalizeMsalConfig(raw: unknown): MsalConfig | null {
  if (!raw || typeof raw !== 'object') return null
  const clientId = typeof (raw as { client_id?: unknown }).client_id === 'string'
    ? (raw as { client_id: string }).client_id.trim()
    : ''
  const tenantId = typeof (raw as { tenant_id?: unknown }).tenant_id === 'string'
    ? (raw as { tenant_id: string }).tenant_id.trim()
    : ''
  if (!clientId || !tenantId) return null
  return { clientId, tenantId }
}

async function getMsalConfig(): Promise<MsalConfig | null> {
  if (!cachedMsalConfigPromise) {
    cachedMsalConfigPromise = (async () => {
      try {
        const response = await fetch('/api/voice-config')
        if (!response.ok) return null
        return normalizeMsalConfig(await response.json())
      } catch {
        return null
      }
    })()
  }
  return cachedMsalConfigPromise
}

export async function getDelegatedApiHeaders(options?: { interactive?: boolean }): Promise<Record<string, string>> {
  const config = await getMsalConfig()
  if (!config) return {}

  const token = await getWorkIqGraphToken(config, options?.interactive === true)
  if (!token) return {}

  return { Authorization: `Bearer ${token}` }
}

export function resetDelegatedApiAuthCache(): void {
  cachedMsalConfigPromise = null
}
