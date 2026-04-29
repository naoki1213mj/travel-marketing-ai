import { AlertTriangle } from 'lucide-react'
import type { ErrorData } from '../hooks/useSSE'

const ERROR_MESSAGE_KEYS: Partial<Record<string, string>> = {
  WORKIQ_REDIRECT_FAILED: 'error.workiq_redirect_failed',
}

interface ErrorRetryProps {
  error: ErrorData
  onRetry: () => void
  retryLabel: string
  t: (key: string) => string
}

export function ErrorRetry({ error, onRetry, retryLabel, t }: ErrorRetryProps) {
  const translatedMessageKey = error.code ? ERROR_MESSAGE_KEYS[error.code] : undefined
  const resolvedMessage = translatedMessageKey ? t(translatedMessageKey) : error.message

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="rounded-[24px] border border-[var(--danger-border)] bg-[var(--danger-surface)] p-5"
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-3">
        <AlertTriangle className="h-5 w-5 shrink-0 text-[var(--danger-text)]" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-[var(--danger-text)]">
            {t('error.title')}
          </p>
          <p className="mt-1 text-sm text-[var(--danger-text)]/90">
            {resolvedMessage}
          </p>
          {error.code && (
            <p className="mt-1 text-xs text-[var(--danger-text)]/70">
              Code: {error.code}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex w-full items-center justify-center rounded-lg border border-[var(--danger-border)] bg-[var(--panel-bg)] px-3 py-2 text-sm font-medium text-[var(--danger-text)] transition-colors hover:bg-[var(--surface)] sm:w-auto sm:shrink-0"
        >
          {retryLabel}
        </button>
      </div>
    </div>
  )
}
