import { useEffect, useMemo, useRef, useState } from 'react'
import type { AgentProgress, ErrorData, PipelineMetrics, TextContent, ToolEvent } from '../hooks/useSSE'
import { AnalysisView } from './AnalysisView'
import { ErrorRetry } from './ErrorRetry'
import { MarkdownView } from './MarkdownView'
import { MetricsBar } from './MetricsBar'
import { RegulationResults } from './RegulationResults'
import { ToolEventBadges } from './ToolEventBadges'

const STEPS = [
  { key: 'data-search-agent', icon: '📊', labelKey: 'step.data_search', step: 1 },
  { key: 'marketing-plan-agent', icon: '📝', labelKey: 'step.marketing_plan', step: 2 },
  { key: 'regulation-check-agent', icon: '⚖️', labelKey: 'step.regulation', step: 4 },
  { key: 'brochure-gen-agent', icon: '🎨', labelKey: 'step.brochure', step: 5 },
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
  const activeRef = useRef<HTMLDivElement>(null)

  const currentStep = agentProgress?.step ?? 0
  const currentAgent = agentProgress?.agent ?? ''

  // 折りたたみ状態をステップから導出（pure derived state）
  const autoCollapsed = useMemo(() => {
    const result: Record<string, boolean> = {}
    STEPS.forEach((step) => {
      if (step.step < currentStep) result[step.key] = true
      else if (step.key === currentAgent) result[step.key] = false
      else result[step.key] = false
    })
    return result
  }, [currentStep, currentAgent])

  // 手動トグル用の state（ユーザー操作のみ）
  // currentStep が変わったらリセットされる（key として currentStep を使用）
  const [userToggled, setUserToggled] = useState<{ step: number; overrides: Record<string, boolean> }>({ step: 0, overrides: {} })

  // ステップが変わったら overrides を自動リセット
  const activeOverrides = userToggled.step === currentStep ? userToggled.overrides : {}

  const isSectionCollapsed = (key: string): boolean => {
    if (key in activeOverrides) return activeOverrides[key]
    return autoCollapsed[key] ?? false
  }

  const toggle = (key: string) => setUserToggled({ step: currentStep, overrides: { ...activeOverrides, [key]: !isSectionCollapsed(key) } })

  // アクティブセクションにスクロール
  useEffect(() => {
    activeRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [currentAgent])

  const getStatus = (stepKey: string, stepNum: number) => {
    if (!agentProgress) return 'pending'
    if (stepNum < agentProgress.step) return 'completed'
    if (agentProgress.agent === stepKey && agentProgress.status === 'running') return 'active'
    if (agentProgress.agent === stepKey && agentProgress.status === 'completed') return 'completed'
    return 'pending'
  }

  const getContent = (agentKey: string) => textContents.find(c => c.agent === agentKey)
  const getToolEvents = (agentKey: string) => toolEvents.filter(e => e.agent === agentKey)

  return (
    <div className="space-y-2">
      {STEPS.map((step) => {
        const status = getStatus(step.key, step.step)
        const content = getContent(step.key)
        const sectionCollapsed = isSectionCollapsed(step.key)
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
              aria-expanded={!sectionCollapsed}
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">{step.icon}</span>
                <span className="text-sm font-medium">{t(step.labelKey)}</span>
                {status === 'completed' && (
                  <span className="rounded-full bg-green-100 dark:bg-green-900/60 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-200">
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
                className={`h-4 w-4 text-[var(--text-muted)] transition-transform duration-200 ${sectionCollapsed ? '' : 'rotate-180'}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {/* 折りたたみ時のサマリー */}
            {sectionCollapsed && content && (
              <p className="px-4 pb-3 text-xs text-[var(--text-muted)] line-clamp-1">
                {content.content.replace(/[#*_]/g, '').slice(0, 120)}…
              </p>
            )}

            {/* 展開時のコンテンツ */}
            {!sectionCollapsed && (
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
