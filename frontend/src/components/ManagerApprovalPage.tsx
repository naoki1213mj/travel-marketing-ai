import { Check, LoaderCircle, RefreshCcw, ShieldAlert, X } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { MarkdownView } from './MarkdownView'

interface ManagerApprovalPageProps {
  conversationId: string
  approvalToken: string
  t: (key: string) => string
}

interface ManagerApprovalPayload {
  conversation_id: string
  current_version: number
  plan_title: string
  plan_markdown: string
  manager_email?: string
  previous_versions?: Array<{
    version: number
    plan_title: string
    plan_markdown: string
  }>
}

type ManagerApprovalPageStatus = 'loading' | 'ready' | 'submitting' | 'approved' | 'rejected' | 'error'

export function ManagerApprovalPage({ conversationId, approvalToken, t }: ManagerApprovalPageProps) {
  const [status, setStatus] = useState<ManagerApprovalPageStatus>('loading')
  const [payload, setPayload] = useState<ManagerApprovalPayload | null>(null)
  const [comment, setComment] = useState('')
  const [errorMessage, setErrorMessage] = useState('')
  const [selectedPreviousVersion, setSelectedPreviousVersion] = useState<number | null>(null)

  const loadApprovalRequest = useCallback(async () => {
    setStatus('loading')
    setErrorMessage('')

    try {
      const response = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/manager-approval-request`, {
        headers: {
          'X-Manager-Approval-Token': approvalToken,
        },
      })

      if (!response.ok) {
        const errorBody = await response.json().catch(() => null)
        throw new Error(String(errorBody?.error || response.status))
      }

      const nextPayload = await response.json() as ManagerApprovalPayload
      setPayload(nextPayload)
      const latestPreviousVersion = nextPayload.previous_versions?.at(-1)?.version ?? null
      setSelectedPreviousVersion(latestPreviousVersion)
      setStatus('ready')
    } catch (error) {
      setPayload(null)
      setStatus('error')
      setErrorMessage(error instanceof Error ? error.message : t('approval.manager.portal.fetch_error'))
    }
  }, [approvalToken, conversationId, t])

  useEffect(() => {
    void loadApprovalRequest()
  }, [loadApprovalRequest])

  const submitDecision = async (approved: boolean) => {
    if (!approved && !comment.trim()) {
      setErrorMessage(t('approval.manager.portal.comment.required'))
      return
    }

    setStatus('submitting')
    setErrorMessage('')
    try {
      const response = await fetch(`/api/chat/${encodeURIComponent(conversationId)}/manager-approval-callback`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Manager-Approval-Token': approvalToken,
        },
        body: JSON.stringify({
          conversation_id: conversationId,
          approved,
          comment: approved ? '' : comment.trim(),
        }),
      })

      if (!response.ok) {
        const errorBody = await response.json().catch(() => null)
        throw new Error(String(errorBody?.error || response.status))
      }

      setStatus(approved ? 'approved' : 'rejected')
    } catch (error) {
      setStatus('ready')
      setErrorMessage(error instanceof Error ? error.message : t('approval.manager.portal.submit_error'))
    }
  }

  if (status === 'loading') {
    return (
      <div className="flex min-h-[60vh] items-center justify-center rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-6 py-10 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
        <div className="flex items-center gap-3 text-sm text-[var(--text-secondary)]">
          <LoaderCircle className="h-5 w-5 animate-spin text-[var(--accent)]" />
          <span>{t('approval.manager.portal.loading')}</span>
        </div>
      </div>
    )
  }

  if (status === 'error' || !payload) {
    return (
      <div className="rounded-[28px] border border-rose-200 bg-rose-50 px-6 py-8 shadow-[0_18px_55px_rgba(15,23,42,0.06)] dark:border-rose-900/50 dark:bg-rose-950/20">
        <div className="flex items-start gap-3">
          <ShieldAlert className="mt-0.5 h-5 w-5 text-rose-600 dark:text-rose-300" />
          <div>
            <h2 className="text-lg font-semibold text-rose-900 dark:text-rose-100">{t('approval.manager.portal.invalid')}</h2>
            <p className="mt-2 text-sm leading-6 text-rose-800 dark:text-rose-200">{errorMessage || t('approval.manager.portal.fetch_error')}</p>
            <button
              type="button"
              onClick={() => { void loadApprovalRequest() }}
              className="mt-4 inline-flex items-center gap-2 rounded-full border border-rose-300 px-4 py-2 text-sm font-medium text-rose-900 transition-colors hover:bg-rose-100 dark:border-rose-700 dark:text-rose-100 dark:hover:bg-rose-900/40"
            >
              <RefreshCcw className="h-4 w-4" />
              {t('approval.manager.portal.retry')}
            </button>
          </div>
        </div>
      </div>
    )
  }

  if (status === 'approved' || status === 'rejected') {
    const isApproved = status === 'approved'
    return (
      <div className="rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-6 py-8 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
        <div className="flex items-start gap-3">
          {isApproved
            ? <Check className="mt-0.5 h-5 w-5 text-emerald-600" />
            : <X className="mt-0.5 h-5 w-5 text-amber-600" />}
          <div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">
              {isApproved ? t('approval.manager.portal.approved') : t('approval.manager.portal.rejected')}
            </h2>
            <p className="mt-2 text-sm leading-6 text-[var(--text-secondary)]">{payload.plan_title}</p>
            {!isApproved && comment.trim() && (
              <p className="mt-3 whitespace-pre-wrap rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-strong)] px-4 py-3 text-sm text-[var(--text-secondary)]">
                {comment.trim()}
              </p>
            )}
          </div>
        </div>
      </div>
    )
  }

  const previousVersions = payload.previous_versions ?? []
  const comparisonTarget = previousVersions.find(version => version.version === selectedPreviousVersion) ?? previousVersions.at(-1) ?? null

  return (
    <div className="space-y-4 rounded-[28px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-6 py-6 shadow-[0_18px_55px_rgba(15,23,42,0.06)]">
      <div className="border-b border-[var(--panel-border)] pb-4">
        <h2 className="text-xl font-semibold tracking-tight text-[var(--text-primary)]">{payload.plan_title}</h2>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">{t('approval.manager.portal.subtitle')}</p>
        {payload.manager_email && (
          <p className="mt-2 text-xs text-[var(--text-muted)]">{t('settings.manager.email')}: {payload.manager_email}</p>
        )}
      </div>

      {previousVersions.length > 0 && (
        <div className="space-y-3 rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-[var(--text-muted)]">
              {t('approval.manager.portal.compare')}
            </span>
            {previousVersions.map((version) => (
              <button
                key={version.version}
                type="button"
                onClick={() => setSelectedPreviousVersion(version.version)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${comparisonTarget?.version === version.version
                  ? 'bg-[var(--accent-soft)] text-[var(--accent-strong)]'
                  : 'bg-[var(--panel-bg)] text-[var(--text-muted)] hover:text-[var(--text-primary)]'
                }`}
              >
                v{version.version}
              </button>
            ))}
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded-[20px] border border-[var(--accent)]/25 bg-[var(--panel-bg)] p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--accent-strong)]">
                    {t('approval.manager.portal.current_version')}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                    v{payload.current_version} · {payload.plan_title}
                  </p>
                </div>
              </div>
              <MarkdownView content={payload.plan_markdown} />
            </div>

            <div className="rounded-[20px] border border-[var(--panel-border)] bg-[var(--panel-bg)] p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div>
                  <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-[var(--text-muted)]">
                    {t('approval.manager.portal.previous_version')}
                  </p>
                  <p className="mt-1 text-sm font-semibold text-[var(--text-primary)]">
                    {comparisonTarget ? `v${comparisonTarget.version} · ${comparisonTarget.plan_title}` : t('approval.manager.portal.previous_version.empty')}
                  </p>
                </div>
              </div>
              {comparisonTarget
                ? <MarkdownView content={comparisonTarget.plan_markdown} />
                : <p className="text-sm text-[var(--text-secondary)]">{t('approval.manager.portal.previous_version.empty')}</p>}
            </div>
          </div>
        </div>
      )}

      {previousVersions.length === 0 && (
        <div className="rounded-[24px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-4">
          <MarkdownView content={payload.plan_markdown} />
        </div>
      )}

      <div className="space-y-2">
        <label htmlFor="manager-approval-comment" className="text-sm font-medium text-[var(--text-primary)]">
          {t('approval.manager.portal.comment')}
        </label>
        <textarea
          id="manager-approval-comment"
          value={comment}
          onChange={(event) => setComment(event.target.value)}
          placeholder={t('approval.manager.portal.comment.placeholder')}
          rows={4}
          className="w-full rounded-2xl border border-[var(--panel-border)] bg-[var(--panel-bg)] px-4 py-3 text-sm text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent)]"
        />
      </div>

      {errorMessage && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-900/50 dark:bg-rose-950/20 dark:text-rose-100">
          {errorMessage}
        </div>
      )}

      <div className="flex flex-col gap-3 sm:flex-row">
        <button
          type="button"
          onClick={() => { void submitDecision(true) }}
          disabled={status === 'submitting'}
          className="inline-flex items-center justify-center gap-2 rounded-full bg-emerald-600 px-5 py-3 text-sm font-semibold text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {status === 'submitting'
            ? <LoaderCircle className="h-4 w-4 animate-spin" />
            : <Check className="h-4 w-4" />}
          {t('approval.manager.portal.submit_approve')}
        </button>
        <button
          type="button"
          onClick={() => { void submitDecision(false) }}
          disabled={status === 'submitting'}
          className="inline-flex items-center justify-center gap-2 rounded-full border border-amber-300 bg-amber-50 px-5 py-3 text-sm font-semibold text-amber-900 transition-colors hover:bg-amber-100 disabled:cursor-not-allowed disabled:opacity-60 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100 dark:hover:bg-amber-950/50"
        >
          {status === 'submitting'
            ? <LoaderCircle className="h-4 w-4 animate-spin" />
            : <X className="h-4 w-4" />}
          {t('approval.manager.portal.submit_reject')}
        </button>
      </div>
    </div>
  )
}
