import type { PipelineMetrics } from '../hooks/useSSE'

interface MetricsBarProps {
  metrics: PipelineMetrics | null
  t: (key: string) => string
}

export function MetricsBar({ metrics, t }: MetricsBarProps) {
  if (!metrics) return null

  return (
    <div className="flex items-center gap-4 rounded-lg bg-gray-50 px-4 py-2
                    text-xs text-gray-600 dark:bg-gray-800 dark:text-gray-400">
      <span>⏱ {t('metrics.latency')}: {metrics.latency_seconds}s</span>
      <span>🛠 {t('metrics.tools')}: {metrics.tool_calls}</span>
      <span>📝 {t('metrics.tokens')}: {metrics.total_tokens.toLocaleString()}</span>
    </div>
  )
}
