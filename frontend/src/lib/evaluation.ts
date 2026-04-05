export interface EvaluationMetric {
  score: number
  reason?: string
  label?: string
}

export interface CustomEvaluationMetric extends EvaluationMetric {
  details?: Record<string, boolean>
}

export interface EvaluationError {
  error: string
}

export type BuiltinEvaluationResult = Record<string, EvaluationMetric> | EvaluationError

export interface EvaluationQualityTrack {
  overall: number
  summary?: string
  focus_areas?: string[]
  metrics: Record<string, CustomEvaluationMetric>
}

export interface RegressionMetricChange {
  key: string
  label?: string
  area: 'plan' | 'asset'
  current: number
  previous: number
  delta: number
  severity?: 'high' | 'medium'
}

export interface RegressionGuard {
  summary?: string
  has_regressions?: boolean
  degraded_metrics?: RegressionMetricChange[]
  improved_metrics?: RegressionMetricChange[]
  plan_overall_delta?: number
  asset_overall_delta?: number
}

export interface EvaluationResult {
  builtin?: BuiltinEvaluationResult
  custom?: Record<string, CustomEvaluationMetric>
  marketing_quality?: Record<string, number | string>
  plan_quality?: EvaluationQualityTrack
  asset_quality?: EvaluationQualityTrack
  regression_guard?: RegressionGuard
  legacy_overall?: number
  foundry_portal_url?: string
  error?: string
}

export interface EvaluationRecord {
  version: number
  round: number
  createdAt: string
  result: EvaluationResult
}

export interface EvaluationDeltaItem {
  key: string
  label: string
  section: 'plan' | 'asset'
  current: number
  previous: number
  delta: number
  max: number
}

export interface EvaluationDetailChange {
  metricKey: string
  metricLabel: string
  item: string
  current: boolean
  previous: boolean
}

const PLAN_BUILTIN_KEYS = ['relevance', 'coherence', 'fluency'] as const
const MARKETING_KEYS = ['appeal', 'differentiation', 'kpi_validity', 'brand_tone'] as const
const LEGACY_PLAN_CUSTOM_KEYS = [
  'plan_structure_readiness',
  'target_fit_readiness',
  'senior_fit_readiness',
  'kpi_evidence_readiness',
  'offer_specificity',
  'travel_law_compliance',
] as const
const LEGACY_ASSET_CUSTOM_KEYS = [
  'cta_visibility',
  'value_visibility',
  'trust_signal_presence',
  'disclosure_completeness',
  'accessibility_readiness',
] as const
const HIDDEN_BUILTIN_METRICS = new Set(['task_adherence', 'groundedness'])
const LABELS: Record<string, string> = {
  relevance: '依頼適合性',
  coherence: '構成の一貫性',
  fluency: '表現の明瞭さ',
  appeal: '顧客訴求力',
  differentiation: '差別化',
  kpi_validity: 'KPI 妥当性',
  brand_tone: 'ブランド一貫性',
  plan_structure_readiness: '企画書構成の完成度',
  target_fit_readiness: 'ターゲット適合性',
  senior_fit_readiness: 'ターゲット適合性',
  kpi_evidence_readiness: 'KPI 根拠の明確さ',
  offer_specificity: '募集条件の具体性',
  travel_law_compliance: '旅行業法準備度',
  cta_visibility: '予約導線の明確さ',
  value_visibility: 'オファー訴求の明確さ',
  trust_signal_presence: '安心材料の見えやすさ',
  disclosure_completeness: '表示事項の網羅性',
  accessibility_readiness: 'アクセシビリティ準備度',
  conversion_potential: 'コンバージョン期待度',
}

function average(values: number[]): number {
  if (values.length === 0) return -1
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

function normalizeScore(score: number): number {
  if (!Number.isFinite(score) || score < 0) return -1
  if (score <= 1) return score * 5
  return score
}

function cloneTrack(track: EvaluationQualityTrack): EvaluationQualityTrack {
  return JSON.parse(JSON.stringify(track)) as EvaluationQualityTrack
}

function buildTrackSummary(metrics: Record<string, CustomEvaluationMetric>, stableMessage: string): { summary: string; focus_areas: string[] } {
  const ranked = Object.entries(metrics)
    .filter(([, metric]) => Number.isFinite(metric.score) && metric.score >= 0)
    .sort((left, right) => left[1].score - right[1].score)

  const focus_areas = ranked
    .filter(([, metric]) => metric.score < 4)
    .slice(0, 3)
    .map(([key, metric]) => metric.label || LABELS[key] || key)

  if (focus_areas.length === 0) {
    return { summary: stableMessage, focus_areas: [] }
  }

  return {
    summary: `優先補強ポイント: ${focus_areas.join('、')}`,
    focus_areas,
  }
}

function buildDerivedTrack(metrics: Record<string, CustomEvaluationMetric>, stableMessage: string): EvaluationQualityTrack | null {
  const scores = Object.values(metrics)
    .map(metric => metric.score)
    .filter(score => Number.isFinite(score) && score >= 0)

  if (scores.length === 0) return null

  const summary = buildTrackSummary(metrics, stableMessage)
  return {
    overall: average(scores),
    summary: summary.summary,
    focus_areas: summary.focus_areas,
    metrics,
  }
}

function metricLabel(key: string, metric?: CustomEvaluationMetric): string {
  return metric?.label || LABELS[key] || key
}

export function hasBuiltinMetrics(builtin: BuiltinEvaluationResult | undefined): builtin is Record<string, EvaluationMetric> {
  return builtin !== undefined && !('error' in builtin)
}

export function shouldDisplayBuiltinMetric(metricKey: string): boolean {
  return !HIDDEN_BUILTIN_METRICS.has(metricKey)
}

export function cloneEvaluationResult(result: EvaluationResult): EvaluationResult {
  return JSON.parse(JSON.stringify(result)) as EvaluationResult
}

export function cloneEvaluationRecord(record: EvaluationRecord): EvaluationRecord {
  return {
    ...record,
    result: cloneEvaluationResult(record.result),
  }
}

export function getLatestEvaluation(evaluations: EvaluationRecord[]): EvaluationRecord | null {
  return evaluations.length > 0 ? evaluations[evaluations.length - 1] : null
}

function deriveLegacyPlanTrack(result: EvaluationResult): EvaluationQualityTrack | null {
  const metrics: Record<string, CustomEvaluationMetric> = {}

  if (hasBuiltinMetrics(result.builtin)) {
    for (const key of PLAN_BUILTIN_KEYS) {
      const metric = result.builtin[key]
      if (!metric || metric.score < 0) continue
      metrics[key] = {
        ...metric,
        label: metricLabel(key, metric),
        score: normalizeScore(metric.score),
      }
    }
  }

  if (result.marketing_quality) {
    for (const key of MARKETING_KEYS) {
      const value = result.marketing_quality[key]
      if (typeof value !== 'number' || value < 0) continue
      metrics[key] = {
        score: normalizeScore(value),
        reason: typeof result.marketing_quality.reason === 'string' ? result.marketing_quality.reason : undefined,
        label: metricLabel(key),
      }
    }
  }

  if (result.custom) {
    for (const key of LEGACY_PLAN_CUSTOM_KEYS) {
      const metric = result.custom[key]
      if (!metric || metric.score < 0) continue
      const normalizedKey = key === 'senior_fit_readiness' ? 'target_fit_readiness' : key
      if (metrics[normalizedKey]) continue
      metrics[normalizedKey] = {
        ...metric,
        label: metricLabel(normalizedKey, {
          ...metric,
          label: normalizedKey === 'target_fit_readiness' ? LABELS.target_fit_readiness : metric.label,
        }),
        score: normalizeScore(metric.score),
      }
    }
  }

  return buildDerivedTrack(metrics, '主要な企画書観点は安定しています。')
}

function deriveLegacyAssetTrack(result: EvaluationResult): EvaluationQualityTrack | null {
  const metrics: Record<string, CustomEvaluationMetric> = {}

  if (result.custom) {
    for (const key of LEGACY_ASSET_CUSTOM_KEYS) {
      const metric = result.custom[key]
      if (!metric || metric.score < 0) continue
      metrics[key] = {
        ...metric,
        label: metricLabel(key, metric),
        score: normalizeScore(metric.score),
      }
    }

    if (Object.keys(metrics).length === 0 && result.custom.conversion_potential && result.custom.conversion_potential.score >= 0) {
      metrics.value_visibility = {
        ...result.custom.conversion_potential,
        label: metricLabel('conversion_potential', result.custom.conversion_potential),
        score: normalizeScore(result.custom.conversion_potential.score),
      }
    }
  }

  return buildDerivedTrack(metrics, '主要な成果物観点は安定しています。')
}

export function getPlanQuality(result: EvaluationResult): EvaluationQualityTrack | null {
  if (result.plan_quality?.metrics) {
    return cloneTrack(result.plan_quality)
  }
  return deriveLegacyPlanTrack(result)
}

export function getAssetQuality(result: EvaluationResult): EvaluationQualityTrack | null {
  if (result.asset_quality?.metrics) {
    return cloneTrack(result.asset_quality)
  }
  return deriveLegacyAssetTrack(result)
}

export function getRegressionGuard(result: EvaluationResult): RegressionGuard | null {
  return result.regression_guard ? JSON.parse(JSON.stringify(result.regression_guard)) as RegressionGuard : null
}

export function calculateEvaluationOverall(result: EvaluationResult): number {
  const plan = getPlanQuality(result)
  const asset = getAssetQuality(result)
  return average(
    [plan?.overall ?? -1, asset?.overall ?? -1]
      .filter(score => Number.isFinite(score) && score >= 0),
  )
}

function getTrack(result: EvaluationResult, section: 'plan' | 'asset'): EvaluationQualityTrack | null {
  return section === 'plan' ? getPlanQuality(result) : getAssetQuality(result)
}

export function getEvaluationDeltaItems(current: EvaluationResult, previous: EvaluationResult, section: 'plan' | 'asset' = 'plan'): EvaluationDeltaItem[] {
  const currentTrack = getTrack(current, section)
  const previousTrack = getTrack(previous, section)
  if (!currentTrack || !previousTrack) return []

  const items: EvaluationDeltaItem[] = []
  const metricKeys = new Set([...Object.keys(currentTrack.metrics), ...Object.keys(previousTrack.metrics)])
  for (const key of metricKeys) {
    const currentMetric = currentTrack.metrics[key]
    const previousMetric = previousTrack.metrics[key]
    if (!currentMetric || !previousMetric) continue
    if (currentMetric.score < 0 || previousMetric.score < 0) continue
    items.push({
      key,
      label: metricLabel(key, currentMetric),
      section,
      current: currentMetric.score,
      previous: previousMetric.score,
      delta: currentMetric.score - previousMetric.score,
      max: 5,
    })
  }

  return items.sort((left, right) => left.delta - right.delta)
}

export function summarizeEvaluationDiff(items: EvaluationDeltaItem[]): {
  improved: number
  degraded: number
  unchanged: number
} {
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

export function getEvaluationDetailChanges(current: EvaluationResult, previous: EvaluationResult): EvaluationDetailChange[] {
  const changes: EvaluationDetailChange[] = []

  for (const section of ['plan', 'asset'] as const) {
    const currentTrack = getTrack(current, section)
    const previousTrack = getTrack(previous, section)
    if (!currentTrack || !previousTrack) continue

    const metricKeys = new Set([...Object.keys(currentTrack.metrics), ...Object.keys(previousTrack.metrics)])
    for (const key of metricKeys) {
      const currentDetails = currentTrack.metrics[key]?.details
      const previousDetails = previousTrack.metrics[key]?.details
      if (!currentDetails || !previousDetails) continue

      const detailKeys = new Set([...Object.keys(currentDetails), ...Object.keys(previousDetails)])
      for (const item of detailKeys) {
        const currentValue = currentDetails[item]
        const previousValue = previousDetails[item]
        if (typeof currentValue !== 'boolean' || typeof previousValue !== 'boolean') continue
        if (currentValue === previousValue) continue
        changes.push({
          metricKey: key,
          metricLabel: metricLabel(key, currentTrack.metrics[key]),
          item,
          current: currentValue,
          previous: previousValue,
        })
      }
    }
  }

  return changes
}

function collectWeakMetricLabels(track: EvaluationQualityTrack | null, threshold: number): string[] {
  if (!track) return []
  return Object.entries(track.metrics)
    .filter(([, metric]) => metric.score >= 0 && metric.score < threshold)
    .sort((left, right) => left[1].score - right[1].score)
    .slice(0, 4)
    .map(([key, metric]) => metricLabel(key, metric))
}

function collectComparisonRegressions(current: EvaluationResult, previous: EvaluationResult | null): string[] {
  if (!previous) {
    return (getRegressionGuard(current)?.degraded_metrics ?? [])
      .slice(0, 4)
      .map(metric => metric.label || LABELS[metric.key] || metric.key)
  }

  const comparison = [
    ...getEvaluationDeltaItems(current, previous, 'plan'),
    ...getEvaluationDeltaItems(current, previous, 'asset'),
  ]
    .filter(item => item.delta <= -0.35)
    .sort((left, right) => left.delta - right.delta)

  return comparison.slice(0, 4).map(item => item.label)
}

function uniqueLabels(labels: string[]): string[] {
  return [...new Set(labels.filter(Boolean))]
}

export function buildEvaluationFeedback(
  current: EvaluationResult,
  previous: EvaluationResult | null,
  t: (key: string) => string,
): string {
  const planTrack = getPlanQuality(current)
  const assetTrack = getAssetQuality(current)
  const regressed = collectComparisonRegressions(current, previous)
  const weakPlan = collectWeakMetricLabels(planTrack, 4)
  const weakAsset = collectWeakMetricLabels(assetTrack, 3.8)

  const lines: string[] = ['以下の評価結果に基づいて、次ラウンドでは企画書と成果物を改善してください:']

  if (regressed.length > 0) {
    lines.push(`- 前ラウンド比で悪化した項目: ${uniqueLabels(regressed).join('・')}`)
  }

  if (weakPlan.length > 0) {
    lines.push(`- ${t('eval.plan_quality') || '企画書品質'}: ${uniqueLabels(weakPlan).join('・')}を優先補強`)
  }

  if (weakAsset.length > 0) {
    lines.push(`- ${t('eval.asset_quality') || '成果物品質'}: ${uniqueLabels(weakAsset).join('・')}が伝わるよう成果物にも反映`)
  }

  if (lines.length === 1) {
    lines.push('- 主要な品質指標は安定しています。強みを維持しつつ、具体性と予約導線をさらに磨いてください。')
  }

  lines.push('- すでに改善済みの強みは残し、表現条件や注意書きは削除しない')
  return lines.join('\n')
}

export function buildEvaluationQuery(messages: string[]): string {
  const normalized = messages.map(message => message.trim()).filter(Boolean)
  if (normalized.length <= 1) {
    return normalized.join('\n\n')
  }
  return [normalized[0], normalized[normalized.length - 1]].join('\n\n')
}
