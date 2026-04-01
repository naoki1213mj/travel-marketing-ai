import { BarChart3, Check, ChevronDown, FileText, Palette, Scale } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { AgentProgress, ErrorData, PipelineMetrics, TextContent, ToolEvent } from '../hooks/useSSE'
import { AnalysisView } from './AnalysisView'
import { ErrorRetry } from './ErrorRetry'
import { MarkdownView } from './MarkdownView'
import { MetricsBar } from './MetricsBar'
import { RegulationResults } from './RegulationResults'
import { ToolEventBadges } from './ToolEventBadges'

const STEP_ICONS: Record<string, React.ReactNode> = {
  'data-search-agent': <BarChart3 className="h-4 w-4" />,
  'marketing-plan-agent': <FileText className="h-4 w-4" />,
  'regulation-check-agent': <Scale className="h-4 w-4" />,
  'brochure-gen-agent': <Palette className="h-4 w-4" />,
}

/** 全 4 ステップ（Round 1 用） */
const ALL_STEPS = [
  { key: 'data-search-agent', labelKey: 'step.data_search', step: 1 },
  { key: 'marketing-plan-agent', labelKey: 'step.marketing_plan', step: 2 },
  { key: 'regulation-check-agent', labelKey: 'step.regulation', step: 4 },
  { key: 'brochure-gen-agent', labelKey: 'step.brochure', step: 5 },
]

/** Round 2+ 用（データ分析は Round 1 を継承） */
const IMPROVEMENT_STEPS = ALL_STEPS.filter(s => s.key !== 'data-search-agent')

interface Round {
  number: number
  contents: TextContent[]
}

/** textContents を marketing-plan-agent の出現回数でラウンドに分割する */
function splitIntoRounds(textContents: TextContent[]): Round[] {
  if (textContents.length === 0) return []

  const rounds: Round[] = []
  let currentRoundContents: TextContent[] = []
  let marketingPlanCount = 0

  for (const tc of textContents) {
    if (tc.agent === 'marketing-plan-agent') {
      marketingPlanCount++
      if (marketingPlanCount > 1) {
        // 新しいラウンドの境界
        rounds.push({ number: rounds.length + 1, contents: currentRoundContents })
        currentRoundContents = []
      }
    }
    currentRoundContents.push(tc)
  }

  // 最後のラウンド（または唯一のラウンド）
  if (currentRoundContents.length > 0) {
    rounds.push({ number: rounds.length + 1, contents: currentRoundContents })
  }

  return rounds
}

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
  const activeStepKey = currentAgent === 'plan-revision-agent'
    ? 'regulation-check-agent'
    : currentAgent === 'video-gen-agent'
      ? 'brochure-gen-agent'
      : currentAgent

  const rounds = useMemo(() => splitIntoRounds(textContents), [textContents])
  const totalRounds = rounds.length || 1
  const isMultiRound = totalRounds > 1

  // 折りたたみ状態をステップから導出（最新ラウンドのみ適用）
  const autoCollapsed = useMemo(() => {
    const result: Record<string, boolean> = {}
    // 最新ラウンドのコンテンツで「既に結果がある」ステップを判定
    const latestRound = rounds[rounds.length - 1]
    const latestContents = latestRound?.contents ?? []

    ALL_STEPS.forEach((step) => {
      const hasContentInLatest = latestContents.some(c => c.agent === step.key)

      if (!agentProgress) {
        // agentProgress がない（改善開始直後 or 初期状態）: 結果があれば折りたたむ、なければ閉じる
        result[step.key] = hasContentInLatest
        return
      }

      if (step.step < currentStep) {
        result[step.key] = true
      } else if (step.key === activeStepKey) {
        result[step.key] = false
      } else if (currentAgent === 'approval' && step.step > currentStep) {
        result[step.key] = true
      } else if (hasContentInLatest) {
        // 結果があるが active でもない → 折りたたむ
        result[step.key] = true
      } else {
        result[step.key] = false
      }
    })
    return result
  }, [activeStepKey, currentStep, currentAgent, agentProgress, rounds])

  // 手動トグル用の state（ユーザー操作のみ）
  const [userToggled, setUserToggled] = useState<{ step: number; overrides: Record<string, boolean> }>({ step: 0, overrides: {} })
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

  const getStatusForRound = (stepKey: string, stepNum: number, isPastRound: boolean, roundContents: TextContent[]) => {
    if (isPastRound) return 'completed'
    if (!agentProgress) return 'pending'
    const hasContent = roundContents.some(c => c.agent === stepKey)

    // brochure-gen と video-gen は同じ step 5 を共有。
    // video-gen が running のときは brochure セクションも「実行中」扱い
    if (stepKey === 'brochure-gen-agent' && agentProgress.agent === 'video-gen-agent') {
      return agentProgress.status === 'running' ? 'active' : 'completed'
    }

    if (stepKey === 'regulation-check-agent' && agentProgress.agent === 'plan-revision-agent') {
      return agentProgress.status === 'running' ? 'active' : 'completed'
    }

    if (hasContent && agentProgress.agent !== stepKey) return 'completed'
    if (stepNum < agentProgress.step) return 'completed'
    if (agentProgress.agent === stepKey && agentProgress.status === 'running') return 'active'
    if (agentProgress.agent === stepKey && agentProgress.status === 'completed') return 'completed'
    return 'pending'
  }

  const getToolEvents = (agentKey: string) => toolEvents.filter(e => e.agent === agentKey)

  /** 1 つのステップ（アコーディオン項目）を描画する */
  const renderStep = (
    step: { key: string; labelKey: string; step: number },
    roundContents: TextContent[],
    isPastRound: boolean,
  ) => {
    const status = getStatusForRound(step.key, step.step, isPastRound, roundContents)
    const content = roundContents.findLast(c => c.agent === step.key)
    const sectionCollapsed = isPastRound ? true : isSectionCollapsed(step.key)
    const isActive = status === 'active'
    const stepTools = isPastRound ? [] : getToolEvents(step.key)

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
          onClick={() => { if (!isPastRound) toggle(step.key) }}
          className="flex w-full items-center justify-between px-4 py-3 text-left"
        >
          <div className="flex items-center gap-3">
            <span className="text-lg">{STEP_ICONS[step.key]}</span>
            <span className="text-sm font-medium">{t(step.labelKey)}</span>
            {status === 'completed' && (
              <span className="rounded-full bg-green-100 dark:bg-green-900/60 px-2 py-0.5 text-xs font-medium text-green-700 dark:text-green-200">
                <Check className="h-3 w-3" />
              </span>
            )}
            {isActive && (
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 animate-pulse rounded-full bg-[var(--accent)]" />
                <span className="text-xs text-[var(--accent-strong)]">{t('status.running')}</span>
              </span>
            )}
          </div>
          {!isPastRound && (
            <svg
              className={`h-4 w-4 text-[var(--text-muted)] transition-transform duration-200 ${sectionCollapsed ? '' : 'rotate-180'}`}
              fill="none" viewBox="0 0 24 24" stroke="currentColor"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          )}
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
                <AnalysisView contents={roundContents} t={t} />
              ) : step.key === 'regulation-check-agent' ? (
                <RegulationResults contents={roundContents} t={t} />
              ) : step.key === 'brochure-gen-agent' ? (
                <div className="py-3 space-y-2">
                  <p className="text-sm text-[var(--text-secondary)]">{t('workflow.brochure.ready')}</p>
                  <p className="text-xs text-[var(--text-muted)]">{t('workflow.brochure.preview_hint')}</p>
                  {(() => {
                    const videoContent = roundContents.find(c => c.content_type === 'video')
                    const videoProgress = roundContents.find(c => c.agent === 'video-gen-agent' && c.content_type !== 'video')
                    if (videoContent) {
                      return <p className="text-sm text-[var(--text-secondary)]">{t('workflow.video.ready')}</p>
                    }
                    if (videoProgress) {
                      return <p className="text-sm text-[var(--text-muted)]">{t('workflow.video.running')}</p>
                    }
                    return null
                  })()}
                </div>
              ) : (
                <MarkdownView content={content.content} />
              )
            ) : isActive ? (
              <div className="space-y-3 py-4">
                <div className="h-3 w-3/4 animate-pulse rounded-full bg-[var(--panel-border)]" />
                <div className="h-3 w-1/2 animate-pulse rounded-full bg-[var(--panel-border)]" />
                <div className="h-3 w-2/3 animate-pulse rounded-full bg-[var(--panel-border)]" />
              </div>
            ) : null}
          </div>
        )}
      </div>
    )
  }

  /** 1 つのラウンドを描画する */
  const renderRound = (round: Round, isLatest: boolean) => {
    const isPastRound = !isLatest
    const steps = round.number === 1 ? ALL_STEPS : IMPROVEMENT_STEPS

    const roundAccordion = (
      <div className="space-y-2">
        {steps.map((step) => renderStep(step, round.contents, isPastRound))}
      </div>
    )

    if (isPastRound) {
      return (
        <details key={`round-${round.number}`} className="group">
          <summary className="flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-xs text-[var(--text-muted)] hover:bg-[var(--surface)] hover:text-[var(--text-primary)]">
            <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
            <span className="font-medium">
              {round.number === 1 ? t('round.initial') : `${t('workflow.round').replace('{n}', String(round.number))} · ${t('round.improvement')}`}
            </span>
            <span className="rounded-full bg-green-100 dark:bg-green-900/60 px-2 py-0.5 text-[10px] font-medium text-green-700 dark:text-green-200">
              <Check className="inline h-3 w-3" />
            </span>
          </summary>
          <div className="mt-2">
            {roundAccordion}
          </div>
        </details>
      )
    }

    return <div key={`round-${round.number}`}>{roundAccordion}</div>
  }

  return (
    <div className="space-y-2">
      {/* 単一ラウンドの場合 — ラウンド表示なし */}
      {!isMultiRound && (
        <div className="space-y-2">
          {ALL_STEPS.map((step) => {
            const roundContents = rounds[0]?.contents ?? textContents
            return renderStep(step, roundContents, false)
          })}
        </div>
      )}

      {/* 複数ラウンドの場合 */}
      {isMultiRound && rounds.map((round, idx) => {
        const isLatest = idx === rounds.length - 1

        return (
          <div key={`round-wrapper-${round.number}`}>
            {/* ラウンド 2+ にはディバイダーを表示 */}
            {round.number > 1 && (
              <div className="flex items-center gap-3 py-2">
                <div className="h-px flex-1 bg-[var(--panel-border)]" />
                <span className="text-[10px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                  {t('workflow.round').replace('{n}', String(round.number))} · {t('round.improvement')}
                </span>
                <div className="h-px flex-1 bg-[var(--panel-border)]" />
              </div>
            )}
            {renderRound(round, isLatest)}
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
