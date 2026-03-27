import type { ErrorData } from '../hooks/useSSE'

interface ErrorRetryProps {
  error: ErrorData
  onRetry: () => void
  retryLabel: string
}

export function ErrorRetry({ error, onRetry, retryLabel }: ErrorRetryProps) {
  return (
    <div className="rounded-lg bg-red-50 p-4 dark:bg-red-950">
      <div className="flex items-start gap-3">
        <span className="text-lg">⚠️</span>
        <div className="flex-1">
          <p className="text-sm font-medium text-red-800 dark:text-red-300">
            エラーが発生しました
          </p>
          <p className="mt-1 text-sm text-red-600 dark:text-red-400">
            {error.message}
          </p>
          {error.code && (
            <p className="mt-1 text-xs text-red-400 dark:text-red-500">
              Code: {error.code}
            </p>
          )}
        </div>
        <button
          onClick={onRetry}
          className="rounded-lg bg-red-100 px-3 py-1.5 text-sm text-red-700
                     hover:bg-red-200 dark:bg-red-900 dark:text-red-300 dark:hover:bg-red-800"
        >
          🔄 {retryLabel}
        </button>
      </div>
    </div>
  )
}
