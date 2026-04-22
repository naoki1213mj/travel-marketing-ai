export const MSAL_REDIRECT_FAILURE_KEY = 'workIqMsalRedirectFailure'

export type MsalRedirectFailureStage = 'redirect_bridge' | 'main_app'

export interface MsalRedirectFailureSentinel {
  stage: MsalRedirectFailureStage
  message: string
  createdAt: string
}

const DEFAULT_FAILURE_MESSAGE = 'Microsoft 365 sign-in could not be completed.'

function describeError(error: unknown): string {
  if (error instanceof Error) {
    const message = error.message.trim()
    return message || DEFAULT_FAILURE_MESSAGE
  }
  if (typeof error === 'string') {
    const message = error.trim()
    return message || DEFAULT_FAILURE_MESSAGE
  }
  return DEFAULT_FAILURE_MESSAGE
}

export function clearMsalRedirectFailureSentinel(): void {
  window.sessionStorage.removeItem(MSAL_REDIRECT_FAILURE_KEY)
}

export function recordMsalRedirectFailureSentinel(stage: MsalRedirectFailureStage, error: unknown): void {
  window.sessionStorage.setItem(MSAL_REDIRECT_FAILURE_KEY, JSON.stringify({
    stage,
    message: describeError(error),
    createdAt: new Date().toISOString(),
  } satisfies MsalRedirectFailureSentinel))
}

export function readMsalRedirectFailureSentinel(): MsalRedirectFailureSentinel | null {
  const raw = window.sessionStorage.getItem(MSAL_REDIRECT_FAILURE_KEY)
  if (!raw) return null

  try {
    const parsed = JSON.parse(raw) as Partial<MsalRedirectFailureSentinel>
    if (parsed.stage !== 'redirect_bridge' && parsed.stage !== 'main_app') {
      return null
    }
    const createdAt = typeof parsed.createdAt === 'string' && parsed.createdAt.trim()
      ? parsed.createdAt
      : new Date(0).toISOString()
    return {
      stage: parsed.stage,
      message: typeof parsed.message === 'string' && parsed.message.trim()
        ? parsed.message.trim()
        : DEFAULT_FAILURE_MESSAGE,
      createdAt,
    }
  } catch {
    return null
  }
}

export function consumeMsalRedirectFailureSentinel(): MsalRedirectFailureSentinel | null {
  const sentinel = readMsalRedirectFailureSentinel()
  if (sentinel) {
    clearMsalRedirectFailureSentinel()
  }
  return sentinel
}