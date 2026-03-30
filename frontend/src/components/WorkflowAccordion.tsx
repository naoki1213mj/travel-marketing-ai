import { useState, useEffect, useRef } from 'react'
import type { AgentProgress, TextContent, ToolEvent, PipelineMetrics, ErrorData } from '../hooks/useSSE'
import { AnalysisView } from './AnalysisView'
import { MarkdownView } from './MarkdownView'
import { RegulationResults } from './RegulationResults'
import { ToolEventBadges } from './ToolEventBadges'
import { MetricsBar } from './MetricsBar'
import { ErrorRetry } from './ErrorRetry'

const STEPS = [
  { key: 'data-search-agent', icon: '📊', labelKey: 'step.data_search' },
  { key: 'marketing-plan-agent', icon: '📝', labelKey: 'step.marketing_plan' },
  { key: 'regulation-check-agent', icon: '⚖️', labelKey: 'step.regulation' },
  { key: 'brochure-gen-agent', icon: '🎨', labelKey: 'step.brochure' },
]

interface Props {
  agentProgress: AgentProgress | null
  textContents: TextContent[]
  toolEvents: ToolEvent[]
  metrics: PipelineMetrics | null
  error: ErrorData | null
  onRetry: () => void
  t: (key: string) => string
  locale: string
}

export function WorkflowAccordion({ agentProgress, textContents, toolEvents, metrics, error, onRetry, t, locale }: Props) {
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const activeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!agentProgress) return
    setCollapsed(prev => {
      const next = { ...prev }
      STEPS.forEach((step, i) => {
        if (i + 1 < agentProgress.step) next[step.key] = true
      })
      next[agentProgress.agent] = false
      return next
    })
    setTimeout(() => activeRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100)
  }, [agentProgress?.step, agentProgress?.agent])

  const toggle = (key: string) => setCollapsed(p => ({ ...p, [key]: !p[key] }))

  const getStatus = (stepKey: string, stepIndex: number) => {
    if (!agentProgress) return 'pending'
    if (stepIndex + 1 < agentProgress.step) return 'completed'
    if (agentProgress.agent === stepKey && agentProgress.status === 'running') return 'active'
    if (agentProgress.agent === stepKey && agentProgress.status === 'completed') return 'completed'
    return 'pending'
  }

  const getContent = (agentKey: string) => textContents.find(c => c.agent === agentKey)
  const getToolEvents = (agentKey: string) => toolEvents.filter(e => e.agent === agentKey)

  return (
    <div className="space-y-2">
      {STEPS.map((step, i) => {
        const status = getStatus(step.key, i)
        const content = getContent(step.key)
        const isCollapsed = collapsed[step.key] ?? false
        const isActive = status === 'active'
        const stepTools = getToolEvents(step.key)

        return (
          <div
            key={step.key}
            ref={isActive ? activeRef : undefined}
            className={`rounded-2xl border transition-all duration-300 ${
              isActive
                ? 'border-[var(--accent)] bg-[var(--accent-soft)] shadow-md'
                : status === 'completed'
                ? 'border-green-200 dark:border-green-800/30 bg-green-50/50 dark:bg-green-950/10'
                : 'border-[var(--panel-border)] bg-[var(--surface)]'
            }`}
          >
            {/* セクションヘッダー */}
            <button
              onClick={() => toggle(step.key)}
              className="flex w-full items-center justify-between px-4 py-3 text-left"
              aria-expanded={!isCollapsed}
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">{step.icon}</span>
                <span className="text-sm font-medium">{t(step.labelKey)}</span>
                {status === 'completed' && (
                  <span className="rounded-full bg-green-100 dark:bg-green-900/40 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-300">
                    ✓
                  </span>
                )}
                {isActive && (
                  <span className="flex items-center gap-1.5">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" />
                    <span className="text-xs text-[var(--accent-strong)]">{t('status.running')}</span>
                  </span>
                )}
              </div>
              <svg
                className={`h-4 w-4 text-[var(--text-muted)] transition-transform duration-200 ${isCollapsed ? '' : 'rotate-180'}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* 折りたたみ時のサマリー */}
            {isCollapsed && content && (
              <p className="px-4 pb-3 text-xs text-[var(--text-muted)] line-clamp-1">
                {content.content.replace(/[#*_]/g, '').slice(0, 120)}…
              </p>
            )}

            {/* 展開時のコンテンツ */}
            {!isCollapsed && (
              <div className="px-4 pb-4">
                {stepTools.length > 0 && <ToolEventBadges events={stepTools} t={t} />}
                {content ? (
                  step.key === 'data-search-agent' ? (
                    <AnalysisView contents={textContents} t={t} />
                  ) : step.key === 'regulation-check-agent' ? (
                    <RegulationResults contents={textContents} t={t} />
                  ) : (
                    <MarkdownView content={content.content} />
                  )
                ) : isActive ? (
                  <div className="flex items-center gap-2 py-4 text-sm text-[var(--text-muted)]">
                    <div className="h-4 w-4 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
                    {t('status.running')}…
                  </div>
                ) : null}
              </div>
            )}
          </div>
        )
      })}

      {/* エラー表示 */}
      {error && <ErrorRetry error={error} onRetry={onRetry} retryLabel={t('error.retry')} t={t} />}

      {/* メトリクス表示 */}
      {metrics && <MetricsBar metrics={metrics} t={t} locale={locale} />}
    </div>
  )
}
