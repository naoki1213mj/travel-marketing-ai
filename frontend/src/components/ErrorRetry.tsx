import type { ErrorData } from '../hooks/useSSE'

interface ErrorRetryProps {
  error: ErrorData
  onRetry: () => void
  retryLabel: string
  t: (key: string) => string
}

export function ErrorRetry({ error, onRetry, retryLabel, t }: ErrorRetryProps) {
  return (
    <div className="rounded-[24px] border border-[var(--danger-border)] bg-[var(--danger-surface)] p-5">
      <div className="flex items-start gap-3">
        <span className="text-lg">⚠️</span>
        <div className="flex-1">
          <p className="text-sm font-medium text-[var(--danger-text)]">
            {t('error.title')}
          </p>
          <p className="mt-1 text-sm text-[var(--danger-text)]/90">
            {error.message}
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
          className="rounded-lg border border-[var(--danger-border)] bg-[var(--panel-bg)] px-3 py-1.5 text-sm text-[var(--danger-text)]"
        >
          {retryLabel}
        </button>
      </div>
    </div>
  )
}
