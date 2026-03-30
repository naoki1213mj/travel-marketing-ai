import { useState } from 'react'
import type { ApprovalRequest } from '../hooks/useSSE'
import { MarkdownView } from './MarkdownView'

interface PlanApprovalProps {
  request: ApprovalRequest
  onApprove: (response: string) => void
  t: (key: string) => string
}

export function PlanApproval({ request, onApprove, t }: PlanApprovalProps) {
  const [revision, setRevision] = useState('')
  const [mode, setMode] = useState<'view' | 'revise'>('view')

  return (
    <div className="space-y-4 rounded-[24px] border border-[var(--warning-border)] bg-[var(--warning-surface)] p-5">
      <h3 className="text-sm font-medium text-[var(--warning-text)]">
        ✅ {t('approval.title')}
      </h3>

      {request.plan_markdown && (
        <div className="rounded-[20px] border border-[var(--panel-border)] bg-[var(--panel-strong)] p-4">
          <MarkdownView content={request.plan_markdown} />
        </div>
      )}

      <p className="text-sm text-[var(--text-secondary)]">{request.prompt}</p>

      {mode === 'view' ? (
        <div className="flex gap-3">
          <button
            onClick={() => setMode('revise')}
            type="button"
            className="rounded-full border border-[var(--panel-border)] bg-[var(--panel-bg)] px-4 py-2 text-sm font-medium
                       text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
            autoFocus
          >
            {t('approval.revise')}
          </button>
          <button
            type="button"
            onClick={() => onApprove(t('approval.approve'))}
            className="rounded-full bg-green-600 px-4 py-2 text-sm font-medium text-white
                       hover:bg-green-700 dark:bg-green-700 dark:hover:bg-green-800"
          >
            {t('approval.approve')}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <textarea
            value={revision}
            onChange={e => setRevision(e.target.value)}
            placeholder={t('approval.prompt')}
            rows={3}
            className="w-full resize-none rounded-[20px] border border-[var(--panel-border)] bg-[var(--panel-bg)] px-3 py-2
                       text-sm focus:border-[var(--accent)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-soft)]
                       text-[var(--text-primary)] placeholder:text-[var(--text-muted)]"
            autoFocus
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setMode('view')}
              className="rounded-full border border-[var(--panel-border)] px-3 py-1.5 text-sm text-[var(--text-secondary)]"
            >
              {t('approval.back')}
            </button>
            <button
              type="button"
              onClick={() => { if (revision.trim()) onApprove(revision.trim()) }}
              disabled={!revision.trim()}
              className="rounded-full bg-[var(--accent)] px-3 py-1.5 text-sm text-white
                         dark:bg-teal-700 dark:text-white
                         hover:bg-blue-700 disabled:opacity-40
              "
            >
              {t('input.send')}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
