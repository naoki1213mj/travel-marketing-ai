export interface EvaluationMetric {
  score: number
  reason?: string
}

export interface CustomEvaluationMetric {
  score: number
  details?: Record<string, boolean>
  reason?: string
}

export interface EvaluationError {
  error: string
}

export type BuiltinEvaluationResult = Record<string, EvaluationMetric> | EvaluationError

export interface EvaluationResult {
  builtin?: BuiltinEvaluationResult
  custom?: Record<string, CustomEvaluationMetric>
  marketing_quality?: Record<string, number | string>
  foundry_portal_url?: string
  error?: string
}

export interface EvaluationRecord {
  version: number
  round: number
  createdAt: string
  result: EvaluationResult
}

function average(values: number[]): number {
  if (values.length === 0) return -1
  return values.reduce((sum, value) => sum + value, 0) / values.length
}

export function hasBuiltinMetrics(builtin: BuiltinEvaluationResult | undefined): builtin is Record<string, EvaluationMetric> {
  return builtin !== undefined && !('error' in builtin)
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

function getBuiltinAverage(result: EvaluationResult): number {
  if (!hasBuiltinMetrics(result.builtin)) return -1

  const scores = Object.values(result.builtin)
    .map(metric => metric.score)
    .filter(score => Number.isFinite(score) && score >= 0)

  return average(scores)
}

function getMarketingAverage(result: EvaluationResult): number {
  const marketing = result.marketing_quality
  if (!marketing) return -1

  const overall = marketing.overall
  if (typeof overall === 'number' && overall >= 0) {
    return overall
  }

  const scores = ['appeal', 'differentiation', 'kpi_validity', 'brand_tone']
    .map(key => marketing[key])
    .filter((value): value is number => typeof value === 'number' && value >= 0)

  return average(scores)
}

function getCustomAverage(result: EvaluationResult): number {
  if (!result.custom) return -1

  const scores = Object.values(result.custom)
    .map(metric => metric.score)
    .filter(score => Number.isFinite(score) && score >= 0)
    .map(score => score * 5)

  return average(scores)
}

export function calculateEvaluationOverall(result: EvaluationResult): number {
  const categoryScores = [
    getBuiltinAverage(result),
    getMarketingAverage(result),
    getCustomAverage(result),
  ].filter(score => Number.isFinite(score) && score >= 0)

  return average(categoryScores)
}
