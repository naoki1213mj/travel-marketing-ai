import { AlertTriangle, CheckCircle, ExternalLink, MessageSquare, Search, Sparkles, TrendingDown, TrendingUp, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { ArtifactSnapshot } from '../hooks/useSSE'
import {
  buildEvaluationFeedback,
  calculateEvaluationOverall,
  getAssetQuality,
  getEvaluationDeltaItems,
  getEvaluationDetailChanges,
  getLatestEvaluation,
  getPlanQuality,
  getRegressionGuard,
  type EvaluationDeltaItem,
  type EvaluationQualityTrack,
  type EvaluationRecord,
  type EvaluationResult,
  type RegressionGuard,
  type RegressionMetricChange
} from '../lib/evaluation'

interface EvaluationPanelProps {
  query: string
  response: string
  html: string
  t: (key: string) => string
  conversationId?: string | null
  artifactVersion?: number
  evaluations?: EvaluationRecord[]
  versions?: ArtifactSnapshot[]
  isLatestVersion?: boolean
  onEvaluationRecorded?: (record: EvaluationRecord) => void
  onRefine?: (feedback: string) => void
}

interface ComparisonSummary {
  version: number
  latest: EvaluationRecord
  composite: number
  planOverall: number
  assetOverall: number
}

const PLAN_GROUPS = [
  { titleKey: 'eval.ai_review', keys: ['relevance', 'coherence', 'fluency'] },
  { titleKey: 'eval.marketing_review', keys: ['appeal', 'differentiation', 'kpi_validity', 'brand_tone'] },
  {
    titleKey: 'eval.execution_readiness',
    keys: ['plan_structure_readiness', 'senior_fit_readiness', 'kpi_evidence_readiness', 'offer_specificity', 'travel_law_compliance'],
  },
]

const ASSET_GROUPS = [
  {
    titleKey: 'eval.asset_readiness',
    keys: ['cta_visibility', 'value_visibility', 'trust_signal_presence', 'disclosure_completeness', 'accessibility_readiness'],
  },
]

function ScoreBadge({ score, max = 5 }: { score: number; max?: number }) {
  if (score < 0) return <span className="text-xs text-[var(--text-muted)]">N/A</span>
  const pct = (score / max) * 100
  const color = pct >= 80 ? 'text-green-500' : pct >= 60 ? 'text-yellow-500' : 'text-red-400'
  return (
    <span className={`text-lg font-semibold ${color}`}>
      {score.toFixed(1)}
      <span className="ml-0.5 text-xs font-normal text-[var(--text-muted)]">/{max}</span>
    </span>
  )
}

function ScoreDelta({ current, previous }: { current: number; previous: number }) {
  if (current < 0 || previous < 0) return null
  const delta = current - previous
  if (Math.abs(delta) < 0.05) return null
  const isUp = delta > 0
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${isUp ? 'text-green-500' : 'text-red-400'}`}>
      {isUp ? <TrendingUp className="h-3.5 w-3.5" /> : <TrendingDown className="h-3.5 w-3.5" />}
      {isUp ? '+' : ''}{delta.toFixed(1)}
    </span>
  )
}

function CheckItem({ label, passed }: { label: string; passed: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-[var(--panel-border)] bg-[var(--panel-bg)] px-2.5 py-1 text-[11px] text-[var(--text-secondary)]">
      {passed ? <CheckCircle className="h-3.5 w-3.5 text-green-500" /> : <XCircle className="h-3.5 w-3.5 text-red-400" />}
      <span>{label}</span>
    </span>
  )
}

function SummaryCard({
  eyebrow,
  title,
  score,
  previous,
  summary,
  accent = false,
}: {
  eyebrow: string
  title: string
  score: number
  previous?: number
  summary?: string
  accent?: boolean
}) {
  return (
    <div className={`rounded-3xl border p-4 ${accent ? 'border-[var(--accent)]/30 bg-[var(--accent-soft)]/60' : 'border-[var(--panel-border)] bg-[var(--panel-bg)]'}`}>
      <p className={`text-[10px] font-semibold uppercase tracking-[0.2em] ${accent ? 'text-[var(--accent-strong)]' : 'text-[var(--text-muted)]'}`}>
        {eyebrow}
      </p>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">{title}</p>
          <div className="mt-2"><ScoreBadge score={score} /></div>
        </div>
        {typeof previous === 'number' && <ScoreDelta current={score} previous={previous} />}
      </div>
      {summary && <p className="mt-3 text-xs leading-5 text-[var(--text-secondary)]">{summary}</p>}
    </div>
  )
}

function ComparisonVersionCard({
  label,
  version,
  roundLabel,
  composite,
  planOverall,
  assetOverall,
  accent = false,
}: {
  label: string
  version: number
  roundLabel: string
  composite: number
  planOverall: number
  assetOverall: number
  accent?: boolean
}) {
  return (
    <div className={`rounded-3xl border p-4 ${accent ? 'border-[var(--accent)]/30 bg-[var(--accent-soft)]/60' : 'border-[var(--panel-border)] bg-[var(--panel-bg)]'}`}>
      <p className={`text-[10px] font-semibold uppercase tracking-[0.18em] ${accent ? 'text-[var(--accent-strong)]' : 'text-[var(--text-muted)]'}`}>
        {label}
      </p>
      <div className="mt-3 flex items-end justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">v{version}</p>
          <p className="mt-1 text-[11px] text-[var(--text-muted)]">{roundLabel}</p>
        </div>
        <ScoreBadge score={composite} />
      </div>
      <div className="mt-3 grid grid-cols-2 gap-3 text-xs text-[var(--text-secondary)]">
        <div>
          <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--text-muted)]">{accent ? 'PLAN' : 'Plan'}</p>
          <div className="mt-1"><ScoreBadge score={planOverall} /></div>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-[0.16em] text-[var(--text-muted)]">Asset</p>
          <div className="mt-1"><ScoreBadge score={assetOverall} /></div>
        </div>
      </div>
    </div>
  )
}

function RegressionCard({ guard, t }: { guard: RegressionGuard | null; t: (key: string) => string }) {
  const degraded = guard?.degraded_metrics ?? []
  const improved = guard?.improved_metrics ?? []

  return (
    <div className="rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-[var(--text-muted)]">{t('eval.regression_guard')}</p>
      <div className="mt-3 flex items-center justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-[var(--text-primary)]">{t('eval.compare')}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <span className="rounded-full bg-red-500/10 px-2.5 py-1 text-red-500">{t('eval.compare.degraded')}: {degraded.length}</span>
            <span className="rounded-full bg-green-500/10 px-2.5 py-1 text-green-600">{t('eval.compare.improved')}: {improved.length}</span>
          </div>
        </div>
        {guard?.has_regressions ? <AlertTriangle className="h-5 w-5 text-red-400" /> : <CheckCircle className="h-5 w-5 text-green-500" />}
      </div>
      <p className="mt-3 text-xs leading-5 text-[var(--text-secondary)]">{guard?.summary || t('eval.regression.none')}</p>
    </div>
  )
}

function MetricCard({
  label,
  score,
  previous,
  reason,
  details,
}: {
  label: string
  score: number
  previous?: number
  reason?: string
  details?: Record<string, boolean>
}) {
  return (
    <div className="rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-3">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-[var(--text-primary)]">{label}</p>
        <div className="text-right">
          <ScoreBadge score={score} />
          {typeof previous === 'number' && <div className="mt-1"><ScoreDelta current={score} previous={previous} /></div>}
        </div>
      </div>
      {reason && <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{reason}</p>}
      {details && (
        <div className="mt-3 flex flex-wrap gap-2">
          {Object.entries(details).map(([item, passed]) => (
            <CheckItem key={item} label={item} passed={passed} />
          ))}
        </div>
      )}
    </div>
  )
}

function DeltaBadge({ item }: { item: RegressionMetricChange }) {
  const isUp = item.delta > 0
  return (
    <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-[11px] font-medium ${isUp ? 'bg-green-500/10 text-green-600' : 'bg-red-500/10 text-red-500'}`}>
      <span>{item.label || item.key}</span>
      <span>{isUp ? '+' : ''}{item.delta.toFixed(1)}</span>
    </span>
  )
}

function getDefaultComparisonVersion(currentVersion: number | undefined, availableVersions: number[]): number | null {
  if (!currentVersion) return null

  const candidates = availableVersions.filter(version => version !== currentVersion)
  if (candidates.length === 0) return null
  if (candidates.includes(currentVersion - 1)) return currentVersion - 1
  return candidates.find(version => version > currentVersion) ?? candidates[candidates.length - 1]
}

function buildComparisonSummaries(versions: ArtifactSnapshot[]): ComparisonSummary[] {
  return versions
    .map((snapshot, index) => {
      const latest = getLatestEvaluation(snapshot.evaluations)
      if (!latest) return null
      const plan = getPlanQuality(latest.result)
      const asset = getAssetQuality(latest.result)
      return {
        version: index + 1,
        latest,
        composite: calculateEvaluationOverall(latest.result),
        planOverall: plan?.overall ?? -1,
        assetOverall: asset?.overall ?? -1,
      }
    })
    .filter((item): item is ComparisonSummary => item !== null)
}

function renderTrackGroups(
  track: EvaluationQualityTrack | null,
  previousTrack: EvaluationQualityTrack | null,
  groups: Array<{ titleKey: string; keys: string[] }>,
  t: (key: string) => string,
) {
  if (!track) return null

  return groups
    .map(group => {
      const items = group.keys
        .map(key => ({ key, metric: track.metrics[key], previous: previousTrack?.metrics[key] }))
        .filter((item): item is { key: string; metric: EvaluationQualityTrack['metrics'][string]; previous?: EvaluationQualityTrack['metrics'][string] } => Boolean(item.metric))

      if (items.length === 0) return null

      return (
        <div key={group.titleKey}>
          <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t(group.titleKey)}</p>
          <div className="grid gap-3 xl:grid-cols-2">
            {items.map(item => (
              <MetricCard
                key={item.key}
                label={item.metric.label || t(`eval.${item.key}`) || item.key}
                score={item.metric.score}
                previous={item.previous?.score}
                reason={item.metric.reason}
                details={item.metric.details}
              />
            ))}
          </div>
        </div>
      )
    })
    .filter(Boolean)
}

export function EvaluationPanel({
  query,
  response,
  html,
  t,
  conversationId,
  artifactVersion,
  evaluations = [],
  versions = [],
  isLatestVersion = true,
  onEvaluationRecorded,
  onRefine,
}: EvaluationPanelProps) {
  const [draftHistories, setDraftHistories] = useState<Record<string, EvaluationRecord[]>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [comparisonVersion, setComparisonVersion] = useState<number | null>(null)

  const evaluationKey = useMemo(
    () => JSON.stringify([conversationId ?? 'draft', artifactVersion ?? 0, query, response]),
    [artifactVersion, conversationId, query, response],
  )
  const history = artifactVersion && artifactVersion > 0
    ? evaluations
    : (draftHistories[evaluationKey] ?? [])

  const latestRecord = getLatestEvaluation(history)
  const previousRecord = history.length > 1 ? history[history.length - 2] : null
  const result = latestRecord?.result ?? null
  const previousResult = previousRecord?.result ?? null
  const planTrack = result ? getPlanQuality(result) : null
  const previousPlanTrack = previousResult ? getPlanQuality(previousResult) : null
  const assetTrack = result ? getAssetQuality(result) : null
  const previousAssetTrack = previousResult ? getAssetQuality(previousResult) : null
  const regressionGuard = result ? getRegressionGuard(result) : null

  const versionComparisons = buildComparisonSummaries(versions)
  const availableComparisonVersions = versionComparisons.map(item => item.version)
  const defaultComparisonVersion = getDefaultComparisonVersion(artifactVersion, availableComparisonVersions)

  useEffect(() => {
    setComparisonVersion(previous => {
      if (previous && availableComparisonVersions.includes(previous) && previous !== artifactVersion) {
        return previous
      }
      return defaultComparisonVersion
    })
  }, [artifactVersion, availableComparisonVersions, defaultComparisonVersion])

  const selectedComparison = versionComparisons.find(item => item.version === comparisonVersion) ?? null
  const comparisonPlanItems = result && selectedComparison
    ? getEvaluationDeltaItems(result, selectedComparison.latest.result, 'plan')
    : []
  const comparisonAssetItems = result && selectedComparison
    ? getEvaluationDeltaItems(result, selectedComparison.latest.result, 'asset')
    : []
  const comparisonSummary = useMemo(
    () => summarizeComparison([...comparisonPlanItems, ...comparisonAssetItems]),
    [comparisonAssetItems, comparisonPlanItems],
  )
  const detailChanges = result && selectedComparison
    ? getEvaluationDetailChanges(result, selectedComparison.latest.result)
    : []

  const runEvaluation = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          query,
          response,
          html,
          conversation_id: conversationId,
          artifact_version: artifactVersion,
        }),
      })
      if (!res.ok) {
        setError(`HTTP ${res.status}`)
        return
      }

      const data = await res.json() as EvaluationResult & {
        evaluation_meta?: { version: number; round: number; created_at: string } | null
      }
      const record: EvaluationRecord = {
        version: data.evaluation_meta?.version ?? artifactVersion ?? 1,
        round: data.evaluation_meta?.round ?? (history.length + 1),
        createdAt: data.evaluation_meta?.created_at ?? new Date().toISOString(),
        result: data,
      }

      if (artifactVersion && artifactVersion > 0) {
        onEvaluationRecorded?.(record)
      } else {
        setDraftHistories(prev => ({
          ...prev,
          [evaluationKey]: [...(prev[evaluationKey] ?? []), record],
        }))
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  if (!response) return null

  return (
    <div className="mt-4 space-y-4">
      <div className="flex items-center gap-3">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {t('eval.title')}
        </h4>
        <button
          onClick={runEvaluation}
          disabled={loading}
          className="flex items-center gap-1.5 rounded-full bg-[var(--accent-soft)] px-3 py-1.5 text-xs font-medium text-[var(--accent-strong)] transition-colors hover:bg-[var(--accent)]/20 disabled:opacity-40"
        >
          {loading ? (
            <>
              <span className="h-3 w-3 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
              {t('eval.running')}
            </>
          ) : (
            <><Search className="h-3.5 w-3.5" /> {t('eval.run')}</>
          )}
        </button>
      </div>

      {error && (
        <p className="inline-flex items-center gap-1 text-xs text-red-500"><XCircle className="h-3.5 w-3.5" /> {error}</p>
      )}

      {result && (
        <div className="space-y-4 rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-4">
          <div className="grid gap-3 xl:grid-cols-3">
            <SummaryCard
              eyebrow={t('eval.plan_quality')}
              title={t('eval.plan_quality')}
              score={planTrack?.overall ?? -1}
              previous={previousPlanTrack?.overall}
              summary={planTrack?.summary}
              accent
            />
            <SummaryCard
              eyebrow={t('eval.asset_quality')}
              title={t('eval.asset_quality')}
              score={assetTrack?.overall ?? -1}
              previous={previousAssetTrack?.overall}
              summary={assetTrack?.summary}
            />
            <RegressionCard guard={regressionGuard} t={t} />
          </div>

          {(planTrack?.focus_areas?.length || assetTrack?.focus_areas?.length) ? (
            <div className="rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <p className="text-xs font-medium text-[var(--text-secondary)]">{t('eval.focus_areas')}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {(planTrack?.focus_areas ?? []).map(area => (
                  <span key={`plan-${area}`} className="rounded-full bg-[var(--accent-soft)] px-3 py-1 text-[11px] font-medium text-[var(--accent-strong)]">
                    {t('eval.plan_quality')}: {area}
                  </span>
                ))}
                {(assetTrack?.focus_areas ?? []).map(area => (
                  <span key={`asset-${area}`} className="rounded-full border border-[var(--panel-border)] bg-[var(--surface)] px-3 py-1 text-[11px] font-medium text-[var(--text-secondary)]">
                    {t('eval.asset_quality')}: {area}
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          {versionComparisons.length > 1 && artifactVersion && (
            <div className="rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
                <div>
                  <p className="text-xs font-medium text-[var(--text-secondary)]">{t('eval.compare')}</p>
                  <p className="mt-1 text-[11px] text-[var(--text-muted)]">{t('eval.compare.preview_hint')}</p>
                </div>
                {selectedComparison && (
                  <p className="text-[11px] text-[var(--text-muted)]">
                    {t('eval.compare.selection')
                      .replace('{current}', `v${artifactVersion}`)
                      .replace('{target}', `v${selectedComparison.version}`)}
                  </p>
                )}
              </div>

              {selectedComparison && (
                <div className="mt-3 grid gap-3 lg:grid-cols-2">
                  <ComparisonVersionCard
                    label={t('eval.compare.current')}
                    version={artifactVersion}
                    roundLabel={t('eval.round').replace('{n}', String(latestRecord?.round ?? 1))}
                    composite={calculateEvaluationOverall(result)}
                    planOverall={planTrack?.overall ?? -1}
                    assetOverall={assetTrack?.overall ?? -1}
                    accent
                  />
                  <ComparisonVersionCard
                    label={t('eval.compare.target')}
                    version={selectedComparison.version}
                    roundLabel={t('eval.round').replace('{n}', String(selectedComparison.latest.round))}
                    composite={selectedComparison.composite}
                    planOverall={selectedComparison.planOverall}
                    assetOverall={selectedComparison.assetOverall}
                  />
                </div>
              )}

              {versionComparisons.length > 2 && (
                <div className="mt-3">
                  <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t('eval.compare.switch_target')}</p>
                  <div className="flex flex-wrap gap-2">
                    {versionComparisons
                      .filter(item => item.version !== artifactVersion)
                      .map(item => (
                        <button
                          key={item.version}
                          type="button"
                          onClick={() => setComparisonVersion(item.version)}
                          className={`rounded-full border px-3 py-2 text-left transition-colors ${
                            comparisonVersion === item.version
                              ? 'border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent-strong)]'
                              : 'border-[var(--panel-border)] bg-[var(--surface)] text-[var(--text-secondary)] hover:border-[var(--accent)]/40'
                          }`}
                        >
                          <span className="block text-[10px] uppercase tracking-[0.18em]">v{item.version}</span>
                          <span className="mt-1 block text-xs font-medium">{t('eval.round').replace('{n}', String(item.latest.round))}</span>
                        </button>
                      ))}
                  </div>
                </div>
              )}

              {selectedComparison && (
                <div className="mt-4 space-y-4 rounded-3xl border border-[var(--panel-border)] bg-[var(--surface)] p-4">
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-green-500/10 px-3 py-1 text-[11px] font-medium text-green-600">{t('eval.compare.improved')}: {comparisonSummary.improved}</span>
                    <span className="rounded-full bg-red-500/10 px-3 py-1 text-[11px] font-medium text-red-500">{t('eval.compare.degraded')}: {comparisonSummary.degraded}</span>
                    <span className="rounded-full bg-[var(--panel-bg)] px-3 py-1 text-[11px] font-medium text-[var(--text-secondary)]">{t('eval.compare.unchanged')}: {comparisonSummary.unchanged}</span>
                  </div>

                  {comparisonPlanItems.length > 0 && (
                    <div>
                      <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.plan_quality')}</p>
                      <div className="flex flex-wrap gap-2">
                        {comparisonPlanItems.map(item => (
                          <span key={`plan-${item.key}`} className={`rounded-full px-3 py-1 text-[11px] font-medium ${item.delta >= 0 ? 'bg-green-500/10 text-green-600' : 'bg-red-500/10 text-red-500'}`}>
                            {item.label} {item.delta >= 0 ? '+' : ''}{item.delta.toFixed(1)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {comparisonAssetItems.length > 0 && (
                    <div>
                      <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.asset_quality')}</p>
                      <div className="flex flex-wrap gap-2">
                        {comparisonAssetItems.map(item => (
                          <span key={`asset-${item.key}`} className={`rounded-full px-3 py-1 text-[11px] font-medium ${item.delta >= 0 ? 'bg-green-500/10 text-green-600' : 'bg-red-500/10 text-red-500'}`}>
                            {item.label} {item.delta >= 0 ? '+' : ''}{item.delta.toFixed(1)}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}

                  {detailChanges.length > 0 && (
                    <div>
                      <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.compare.detail_changes')}</p>
                      <div className="flex flex-wrap gap-2">
                        {detailChanges.map(change => (
                          <span
                            key={`${change.metricKey}:${change.item}`}
                            className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-bg)] px-3 py-1.5 text-[11px] text-[var(--text-secondary)]"
                          >
                            {change.metricLabel}: {change.item} · v{artifactVersion} {change.current ? '✓' : '✕'} / v{selectedComparison.version} {change.previous ? '✓' : '✕'}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {regressionGuard && ((regressionGuard.degraded_metrics?.length ?? 0) > 0 || (regressionGuard.improved_metrics?.length ?? 0) > 0) && (
            <div className="rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <div className="flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-amber-500" />
                <p className="text-xs font-medium text-[var(--text-secondary)]">{t('eval.regression_guard')}</p>
              </div>
              <p className="mt-2 text-xs leading-5 text-[var(--text-secondary)]">{regressionGuard.summary}</p>
              {(regressionGuard.degraded_metrics?.length ?? 0) > 0 && (
                <div className="mt-3">
                  <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t('eval.compare.degraded')}</p>
                  <div className="flex flex-wrap gap-2">
                    {regressionGuard.degraded_metrics?.map(item => <DeltaBadge key={`degraded-${item.key}`} item={item} />)}
                  </div>
                </div>
              )}
              {(regressionGuard.improved_metrics?.length ?? 0) > 0 && (
                <div className="mt-3">
                  <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t('eval.compare.improved')}</p>
                  <div className="flex flex-wrap gap-2">
                    {regressionGuard.improved_metrics?.map(item => <DeltaBadge key={`improved-${item.key}`} item={item} />)}
                  </div>
                </div>
              )}
            </div>
          )}

          {planTrack && (
            <div className="space-y-4 rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-medium text-[var(--text-secondary)]">{t('eval.plan_quality')}</p>
                  <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{planTrack.summary}</p>
                </div>
                <div className="text-right">
                  <ScoreBadge score={planTrack.overall} />
                  {typeof previousPlanTrack?.overall === 'number' && <div className="mt-1"><ScoreDelta current={planTrack.overall} previous={previousPlanTrack.overall} /></div>}
                </div>
              </div>
              {renderTrackGroups(planTrack, previousPlanTrack, PLAN_GROUPS, t)}
            </div>
          )}

          {assetTrack && (
            <div className="space-y-4 rounded-3xl border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-medium text-[var(--text-secondary)]">{t('eval.asset_quality')}</p>
                  <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{assetTrack.summary}</p>
                </div>
                <div className="text-right">
                  <ScoreBadge score={assetTrack.overall} />
                  {typeof previousAssetTrack?.overall === 'number' && <div className="mt-1"><ScoreDelta current={assetTrack.overall} previous={previousAssetTrack.overall} /></div>}
                </div>
              </div>
              {renderTrackGroups(assetTrack, previousAssetTrack, ASSET_GROUPS, t)}
            </div>
          )}

          {result.foundry_portal_url && (
            <a
              href={result.foundry_portal_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-[var(--accent-strong)] hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" /> {t('eval.portal')}
            </a>
          )}

          {onRefine && result && isLatestVersion && (
            <button
              onClick={() => {
                const feedback = buildEvaluationFeedback(result, previousResult, t)
                if (feedback) {
                  onRefine(feedback)
                }
              }}
              className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-full border border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-2 text-xs font-medium text-[var(--accent-strong)] transition-colors hover:bg-[var(--accent)]/20"
            >
              <Sparkles className="h-3.5 w-3.5" /> {t('eval.refine')}
            </button>
          )}

          {onRefine && result && !isLatestVersion && (
            <p className="text-xs text-[var(--text-muted)]">{t('eval.refine.latest_only')}</p>
          )}

          {result.error && (
            <p className="inline-flex items-center gap-1 text-xs text-red-500"><MessageSquare className="h-3.5 w-3.5" /> {result.error}</p>
          )}
        </div>
      )}

      {!result && versionComparisons.length > 0 && artifactVersion && artifactVersion > 0 && (
        <div className="rounded-2xl border border-dashed border-[var(--panel-border)] bg-[var(--panel-strong)] px-4 py-3 text-xs text-[var(--text-muted)]">
          {t('eval.no_result')}
        </div>
      )}
    </div>
  )
}

function summarizeComparison(items: EvaluationDeltaItem[]): { improved: number; degraded: number; unchanged: number } {
  return items.reduce(
    (summary, item) => {
      if (item.delta > 0.05) {
        summary.improved += 1
      } else if (item.delta < -0.05) {
        summary.degraded += 1
      } else {
        summary.unchanged += 1
      }
      return summary
    },
    { improved: 0, degraded: 0, unchanged: 0 },
  )
}