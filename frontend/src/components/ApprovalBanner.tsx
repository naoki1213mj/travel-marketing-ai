import { Check, MessageSquareWarning, Pencil } from 'lucide-react';
import { useState } from 'react';
import { ApprovalDiffView } from './ApprovalDiffView';

interface ApprovalBannerProps {
  request: {
    prompt: string
    plan_markdown?: string
    conversation_id: string
    manager_comment?: string
  }
  previousPlanMarkdown?: string
  onApprove: (response: string) => void
  t: (key: string) => string
}

export function ApprovalBanner({ request, previousPlanMarkdown = '', onApprove, t }: ApprovalBannerProps) {
  const [mode, setMode] = useState<'action' | 'revise'>('action')
  const [revision, setRevision] = useState('')
  const displayPrompt = request.prompt.trim() && !/承認|修正|approve|revise/i.test(request.prompt)
    ? request.prompt
    : t('approval.message')

  return (
    <div className="sticky bottom-0 z-30 mx-0 mt-2 rounded-2xl border border-[var(--warning-border)] bg-[var(--warning-surface)] px-6 py-5 shadow-[var(--warning-shadow)] ring-1 ring-[var(--warning-border)] backdrop-blur-lg">
      <h3 className="mb-3 inline-flex items-center gap-2 text-sm font-semibold text-[var(--warning-text)]">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[var(--warning-text)] opacity-50" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-[var(--warning-text)]" />
        </span>
        <MessageSquareWarning className="h-4 w-4" /> {t('approval.title')}
      </h3>

      {request.manager_comment && (
        <div className="mb-3 rounded-xl border border-[var(--warning-border)] bg-[var(--panel-bg)] px-4 py-3 text-sm text-[var(--warning-text)]">
          <p className="font-medium">{t('approval.manager.comment')}</p>
          <p className="mt-1 whitespace-pre-wrap text-xs leading-5">{request.manager_comment}</p>
        </div>
      )}

      {request.plan_markdown && (
        <ApprovalDiffView
          previousText={previousPlanMarkdown}
          currentText={request.plan_markdown}
          previousLabel={t('approval.diff.previous')}
          currentLabel={t('approval.diff.current')}
          className="mb-3"
          t={t}
        />
      )}

      {mode === 'action' ? (
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <p className="flex-1 text-sm font-medium leading-6 text-[var(--text-primary)]">{displayPrompt}</p>
          <button
            type="button"
            onClick={() => setMode('revise')}
            className="inline-flex items-center justify-center gap-1.5 rounded-full border border-[var(--warning-border)] bg-[var(--panel-bg)] px-4 py-2 text-sm font-semibold text-[var(--warning-text)] transition-colors hover:bg-[var(--surface)]"
            autoFocus
          >
            <Pencil className="h-3.5 w-3.5" /> {t('approval.revise')}
          </button>
          <button
            type="button"
            onClick={() => onApprove(t('approval.approve'))}
            className="inline-flex items-center justify-center gap-1.5 rounded-full bg-[var(--approval-approve-bg)] px-6 py-2 text-sm font-semibold text-[var(--approval-approve-text)] shadow-lg shadow-[var(--approval-approve-shadow)] ring-2 ring-[var(--approval-approve-ring)] ring-offset-2 ring-offset-[var(--warning-surface)] transition-all hover:bg-[var(--approval-approve-hover)]"
          >
            <Check className="h-4 w-4" /> {t('approval.approve')}
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          <textarea
            value={revision}
            onChange={e => setRevision(e.target.value)}
            placeholder={t('approval.prompt')}
            className="w-full rounded-xl border border-[var(--warning-border)] bg-[var(--panel-bg)] px-4 py-3 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-[var(--warning-border)]"
            rows={3}
            autoFocus
          />
          <div className="flex gap-2">
            <button type="button" onClick={() => setMode('action')} className="rounded-full border border-[var(--panel-border)] px-4 py-2 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[var(--panel-strong)]">
              {t('approval.back')}
            </button>
            <button
              type="button"
              onClick={() => { onApprove(revision.trim()); setRevision(''); setMode('action') }}
              disabled={!revision.trim()}
              className="rounded-full bg-[var(--warning-action-bg)] px-5 py-2 text-sm font-medium text-[var(--warning-action-text)] transition-colors hover:bg-[var(--warning-action-hover)] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {t('input.send')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
