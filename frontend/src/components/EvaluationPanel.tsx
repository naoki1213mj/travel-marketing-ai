import { CheckCircle, ExternalLink, MessageSquare, Search, Sparkles, XCircle } from 'lucide-react';
import { useMemo, useState } from 'react';

interface EvaluationResult {
  builtin?: Record<string, { score: number; reason?: string }>
  custom?: Record<string, { score: number; details?: Record<string, boolean>; reason?: string }>
  marketing_quality?: Record<string, number | string>
  foundry_portal_url?: string
  error?: string
}

interface EvaluationPanelProps {
  query: string
  response: string
  html: string
  t: (key: string) => string
  onRefine?: (feedback: string) => void
}

function buildEvaluationKey(query: string, response: string): string {
  return JSON.stringify([query, response])
}

function ScoreBadge({ score, max = 5 }: { score: number; max?: number }) {
  if (score < 0) return <span className="text-xs text-[var(--text-muted)]">N/A</span>
  const pct = (score / max) * 100
  const color = pct >= 80 ? 'text-green-500' : pct >= 60 ? 'text-yellow-500' : 'text-red-500'
  return (
    <span className={`text-sm font-bold ${color}`}>
      {score.toFixed(1)}<span className="text-xs font-normal text-[var(--text-muted)]">/{max}</span>
    </span>
  )
}

function CheckItem({ label, passed }: { label: string; passed: boolean }) {
  return (
    <span className="inline-flex items-center gap-1 text-xs">
      <span>{passed ? <CheckCircle className="h-3.5 w-3.5 text-green-500" /> : <XCircle className="h-3.5 w-3.5 text-red-400" />}</span>
      <span className={passed ? 'text-[var(--text-secondary)]' : 'text-red-400'}>{label}</span>
    </span>
  )
}

function ScoreDelta({ current, previous }: { current: number; previous: number }) {
  if (current < 0 || previous < 0) return null
  const delta = current - previous
  if (Math.abs(delta) < 0.05) return null
  const isUp = delta > 0
  return (
    <span className={`ml-1 text-[10px] font-medium ${isUp ? 'text-green-500' : 'text-red-400'}`}>
      {isUp ? '▲' : '▼'} {Math.abs(delta).toFixed(1)}
    </span>
  )
}

/** 評価結果から低スコア項目を抽出し、修正プロンプトを自動生成する */
function buildFeedback(result: EvaluationResult, t: (key: string) => string): string {
  const issues: string[] = []

  // Built-in: スコア 3 未満の指標
  if (result.builtin && !('error' in result.builtin)) {
    for (const [name, val] of Object.entries(result.builtin)) {
      if (val.score >= 0 && val.score < 3) {
        issues.push(`${t(`eval.${name}`) || name}が低い（${val.score}/5）${val.reason ? ': ' + val.reason : ''}`)
      }
    }
  }

  // Marketing quality: スコア 3 未満の項目
  if (result.marketing_quality) {
    for (const key of ['appeal', 'differentiation', 'kpi_validity', 'brand_tone']) {
      const val = result.marketing_quality[key]
      if (typeof val === 'number' && val < 3) {
        issues.push(`${t(`eval.${key}`) || key}が低い（${val}/5）`)
      }
    }
    if (result.marketing_quality.reason) {
      issues.push(`審査コメント: ${String(result.marketing_quality.reason)}`)
    }
  }

  // Compliance: 不適合項目
  if (result.custom) {
    for (const [name, val] of Object.entries(result.custom)) {
      if (val.details) {
        const missing = Object.entries(val.details)
          .filter(([, passed]) => !passed)
          .map(([item]) => item)
        if (missing.length > 0) {
          issues.push(`${t(`eval.${name}`) || name}: ${missing.join('・')}が不足`)
        }
      }
    }
  }

  if (issues.length === 0) {
    return '品質評価の結果、全項目が基準を満たしています。さらにクオリティを高めてください。'
  }

  return `以下の品質評価結果に基づいて企画書を改善してください:\n${issues.map(i => `- ${i}`).join('\n')}`
}

export function EvaluationPanel({ query, response, html, t, onRefine }: EvaluationPanelProps) {
  const [histories, setHistories] = useState<Record<string, EvaluationResult[]>>({})
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const evaluationKey = useMemo(() => buildEvaluationKey(query, response), [query, response])
  const history = histories[evaluationKey] ?? []

  const result = history.length > 0 ? history[history.length - 1] : null
  const previousResult = history.length > 1 ? history[history.length - 2] : null

  const runEvaluation = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, response, html }),
      })
      if (!res.ok) {
        setError(`HTTP ${res.status}`)
        return
      }
      const data = await res.json() as EvaluationResult
      setHistories(prev => ({
        ...prev,
        [evaluationKey]: [...(prev[evaluationKey] ?? []), data],
      }))
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  if (!response) return null

  return (
    <div className="mt-4 space-y-3">
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
        <div className="space-y-3 rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] p-4">
          {history.length > 1 && (
            <p className="text-[10px] font-medium text-[var(--text-muted)]">
              {t('eval.round').replace('{n}', String(history.length))}
            </p>
          )}

          {/* Built-in 評価器 */}
          {result.builtin && !('error' in result.builtin) && (
            <div>
              <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.builtin')}</p>
              <div className="flex flex-wrap gap-4">
                {Object.entries(result.builtin).map(([name, val]) => (
                  <div key={name} className="text-center">
                    <ScoreBadge score={val.score} />
                    {previousResult?.builtin?.[name] != null && (
                      <ScoreDelta current={val.score} previous={previousResult.builtin![name].score} />
                    )}
                    <p className="mt-0.5 text-[10px] text-[var(--text-muted)]">{t(`eval.${name}`) || name}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* カスタム: 企画書品質（Prompt-based LLM ジャッジ） */}
          {result.marketing_quality && !('score' in result.marketing_quality && result.marketing_quality.score === -1) && (
            <div>
              <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.marketing')}</p>
              <div className="flex flex-wrap gap-4">
                {['appeal', 'differentiation', 'kpi_validity', 'brand_tone', 'overall'].map(key => {
                  const val = result.marketing_quality?.[key]
                  const prevVal = previousResult?.marketing_quality?.[key]
                  return typeof val === 'number' ? (
                    <div key={key} className="text-center">
                      <ScoreBadge score={val} />
                      {typeof prevVal === 'number' && <ScoreDelta current={val} previous={prevVal} />}
                      <p className="mt-0.5 text-[10px] text-[var(--text-muted)]">{t(`eval.${key}`) || key}</p>
                    </div>
                  ) : null
                })}
              </div>
              {result.marketing_quality.reason && (
                <p className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--text-secondary)]"><MessageSquare className="h-3 w-3" /> {String(result.marketing_quality.reason)}</p>
              )}
            </div>
          )}

          {/* カスタム: Code-based チェック */}
          {result.custom && (
            <div>
              <p className="mb-2 text-xs font-medium text-[var(--text-secondary)]">{t('eval.compliance')}</p>
              <div className="space-y-2">
                {Object.entries(result.custom).map(([name, val]) => (
                  <div key={name}>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-[var(--text-secondary)]">{t(`eval.${name}`) || name}</span>
                      <ScoreBadge score={val.score} max={1} />
                    </div>
                    {val.details && (
                      <div className="mt-1 flex flex-wrap gap-2">
                        {Object.entries(val.details).map(([item, passed]) => (
                          <CheckItem key={item} label={item} passed={passed as boolean} />
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Foundry ポータルリンク */}
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

          {/* 評価結果に基づく改善ボタン */}
          {onRefine && result && (
            <button
              onClick={() => {
                const feedback = buildFeedback(result, t)
                if (feedback) {
                  onRefine(feedback)
                }
              }}
              className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-full border border-[var(--accent)] bg-[var(--accent-soft)] px-4 py-2 text-xs font-medium text-[var(--accent-strong)] transition-colors hover:bg-[var(--accent)]/20"
            >
              <Sparkles className="h-3.5 w-3.5" /> {t('eval.refine')}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
