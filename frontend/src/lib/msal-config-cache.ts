export interface MsalConfig {
  clientId: string
  tenantId: string
}

export const MSAL_CONFIG_CACHE_KEY = 'workIqMsalConfig'

function readString(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

export function normalizeMsalConfig(raw: unknown): MsalConfig | null {
  if (!raw || typeof raw !== 'object') return null

  const record = raw as {
    client_id?: unknown
    clientId?: unknown
    tenant_id?: unknown
    tenantId?: unknown
  }
  const clientId = readString(record.client_id) || readString(record.clientId)
  const tenantId = readString(record.tenant_id) || readString(record.tenantId)
  if (!clientId || !tenantId) return null

  return { clientId, tenantId }
}

export function readCachedMsalConfig(): MsalConfig | null {
  const raw = window.sessionStorage.getItem(MSAL_CONFIG_CACHE_KEY)
  if (!raw) return null

  try {
    return normalizeMsalConfig(JSON.parse(raw))
  } catch {
    return null
  }
}

export function writeCachedMsalConfig(config: MsalConfig): void {
  window.sessionStorage.setItem(MSAL_CONFIG_CACHE_KEY, JSON.stringify(config))
}

export function clearCachedMsalConfig(): void {
  window.sessionStorage.removeItem(MSAL_CONFIG_CACHE_KEY)
}
