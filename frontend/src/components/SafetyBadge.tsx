import type { SafetyResult } from '../hooks/useSSE'

interface SafetyBadgeProps {
  result: SafetyResult | null
  t: (key: string) => string
}

export function SafetyBadge({ result, t }: SafetyBadgeProps) {
  if (!result) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-gray-100 px-3 py-1
                       text-xs text-gray-500 dark:bg-gray-800 dark:text-gray-400">
        ⚪ {t('safety.checking')}
      </span>
    )
  }

  const isSafe = result.status === 'safe'

  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-3 py-1 text-xs
      ${isSafe
        ? 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300'
        : 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300'
      }`}
    >
      {isSafe ? '🟢' : '🔴'} {isSafe ? t('safety.safe') : t('safety.warning')}
    </span>
  )
}
